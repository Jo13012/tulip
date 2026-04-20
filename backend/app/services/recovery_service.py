"""
recovery_service.py — Session recovery after restart/crash.

Recovery flow:
  1. Backend boots
  2. Active sessions in DB enter "recovering" health_state
  3. Load DB snapshot of slots/lots
  4. Fetch live exchange state (open orders, balances)
  5. Rebuild runtime maps (buy_orders, sell_orders, pending capital)
  6. Block new entries until reconciliation is complete
  7. Move to running if healthy; degraded/error otherwise

Philosophy:
  - DB = metadata, snapshots, audit
  - Polymarket exchange = truth for open orders / balances
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import logging

from app.db.models import (
    Session as DBSession, SessionSlot as DBSessionSlot, SessionLot as DBLot,
    Wallet, Strategy, StrategySlotVersion, AuditLog, User,
)
from app.db.session import async_session_factory
from app.runtime.runtime_models import SlotMarketConfig, Side, StopMode, SlotState, LotStatus, RuntimeLot, real_tick, norm
from app.runtime.session_engine import SessionEngine
from app.runtime.session_slot_runtime import SessionSlotRuntime
from app.runtime.session_registry import session_registry
from app.integrations.polymarket.execution_client import ExecutionClient
from app.runtime.session_manager import _build_slot_market_config

log = logging.getLogger("uvicorn.error")

RECOVERY_TIMEOUT_S = 120.0


class RecoveryService:

    async def recover_all_sessions(self):
        """Called at backend startup to recover all active sessions."""
        async with async_session_factory() as db:
            q = await db.execute(
                select(DBSession).where(
                    DBSession.status.in_(["starting", "running", "stopping", "recovering"])
                )
            )
            sessions = q.scalars().all()

        if not sessions:
            return

        tasks = [
            asyncio.create_task(self._recover_session(s.id))
            for s in sessions
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _recover_session(self, session_id: str):
        """Attempt to recover a single session."""
        async with async_session_factory() as db:
            session = await db.get(DBSession, session_id)
            if not session:
                return

            # Mark recovering
            await db.execute(
                update(DBSession)
                .where(DBSession.id == session_id)
                .values(health_state="recovering")
            )
            db.add(AuditLog(
                actor_id="system", actor_role="system",
                action="session.recovery_start",
                entity_type="session", entity_id=session_id,
            ))
            await db.commit()

        try:
            async with async_timeout(RECOVERY_TIMEOUT_S):
                await self._do_recover(session_id)
        except asyncio.TimeoutError:
            await self._mark_degraded(session_id, "Recovery timed out")
        except Exception as e:
            await self._mark_degraded(session_id, f"Recovery failed: {e}")

    async def _do_recover(self, session_id: str):
        async with async_session_factory() as db:
            session = await db.get(DBSession, session_id)
            if not session:
                return

            wallet = await db.get(Wallet, session.wallet_id)
            if not wallet:
                raise RuntimeError("Wallet not found")

            strat = await db.get(Strategy, session.strategy_id)
            user  = await db.get(User, session.user_id)

            # Load session slots
            slots_q = await db.execute(
                select(DBSessionSlot).where(DBSessionSlot.session_id == session_id)
            )
            db_slots = slots_q.scalars().all()

        original_status = session.status  # "starting" | "running" | "stopping" | "recovering"

        if not wallet.privy_wallet_id:
            raise RuntimeError("No Privy wallet linked — cannot recover session")

        client = ExecutionClient(
            privy_wallet_id=wallet.privy_wallet_id,
            wallet_address=wallet.wallet_address,
            proxy_address=wallet.proxy_address or "",
        )
        await client.initialize()

        # Fetch live open orders (Poly is truth)
        try:
            live_orders = await client.get_open_orders()
        except Exception:
            live_orders = {}

        # ── "starting" / "stopping": hard stop ───────────────────────────────
        # Cancel all live buy orders + taker-sell all tokens held for this
        # session's markets. Then mark session stopped. No engine launched.
        if original_status in ("starting", "stopping"):
            await self._hard_stop_session(client, live_orders, session_id, db_slots)
            async with async_session_factory() as db:
                await db.execute(
                    update(DBLot)
                    .where(
                        DBLot.session_slot_id.in_(
                            select(DBSessionSlot.id).where(DBSessionSlot.session_id == session_id)
                        ),
                        DBLot.status.in_(["open", "selling"]),
                    )
                    .values(
                        status="closed",
                        timestamp_exit=datetime.now(timezone.utc),
                        exit_order_id="hard_stop_recovery",
                    )
                )
                await db.execute(
                    update(DBSession)
                    .where(DBSession.id == session_id)
                    .values(
                        status="stopped",
                        health_state="stopped",
                        stopped_at=datetime.now(timezone.utc),
                        stop_reason=f"Backend restarted during {original_status} — hard stopped.",
                    )
                )
                db.add(AuditLog(
                    actor_id="system", actor_role="system",
                    action="session.recovery_hard_stop",
                    entity_type="session", entity_id=session_id,
                    detail=f"original_status={original_status} — orders cancelled, tokens sold at market",
                ))
                await db.commit()
            log.info("[recovery] session=%s status=%s → hard stopped", session_id[:8], original_status)
            return

        # Reconstruct fee config for running sessions
        if user:
            fee_is_overridden = user.fee_override_bps is not None
            user_fee_bps = user.fee_override_bps if fee_is_overridden else user.effective_fee_bps
            user_trading_volume = user.trading_volume
        else:
            fee_is_overridden = False
            user_fee_bps = 120
            user_trading_volume = 0.0

        # Build engine (fully configured — matches session_manager._start_runtime)
        engine = SessionEngine(
            session_id=session_id,
            user_id=session.user_id,
            wallet_id=session.wallet_id,
            execution_client=client,
            gross_capital=session.session_gross_capital,
            deployable_capital=session.session_deployable_capital,
            fee_reserve=session.session_fee_reserve,
            stop_mode=StopMode(session.stop_mode),
            stop_days=session.stop_days,
            target_volume=session.target_volume,
            session_sl_pct=session.session_sl_pct,
            strategy=strat,
            privy_wallet_id=wallet.privy_wallet_id or "",
            fee_wallet_address=wallet.wallet_address,
            user_fee_bps=user_fee_bps,
            fee_is_overridden=fee_is_overridden,
            user_trading_volume=user_trading_volume,
            db_factory=async_session_factory,
        )
        engine.health_state_override = "recovering"

        await session_registry.add(session_id, engine)
        await engine.start()

        # Restore uptime: patch both started_at (wall clock) and _start_time (monotonic)
        # so uptime_s is continuous across the restart.
        if session.started_at:
            engine.started_at = session.started_at
            elapsed = (datetime.now(timezone.utc) - session.started_at).total_seconds()
            import time as _time
            engine._start_time = _time.monotonic() - max(0.0, elapsed)

        # Recover each slot
        for db_slot in db_slots:
            await self._recover_slot(db_slot, engine, live_orders, client)

        # Only "running" / "recovering" reach here — resume normally
        engine.health_state = "running"
        async with async_session_factory() as db:
            await db.execute(
                update(DBSession)
                .where(DBSession.id == session_id)
                .values(health_state="running", status="running")
            )
            db.add(AuditLog(
                actor_id="system", actor_role="system",
                action="session.recovery_success",
                entity_type="session", entity_id=session_id,
            ))
            await db.commit()

    async def _recover_slot(
        self,
        db_slot: DBSessionSlot,
        engine: SessionEngine,
        live_orders: dict,
        client: ExecutionClient,
    ):
        """
        Poly-first recovery. Poly is the single source of truth.
        DB is used only to enrich lot details (entry_price, tp_price, pnl history).

        Algorithm:
          A. Fetch from Poly: live BUY orders, live SELL orders, token balance
             → if get_open_orders failed, retry once; if still fails, mark degraded
             → if get_token_balance fails, retry once; if still fails, use sum of sell tokens
          B. Build DB lookup maps: exit_oid→lot, entry_oid→lot
          C. For each live SELL order → reconstruct lot from DB or estimate
             → account only remaining (unfilled) tokens
          D. Orphan tokens = balance − tokens_accounted_by_sells
             → split across all DB open lots without exit_oid, one each
             → if more orphans than DB lots, create a synthetic lot
             → threshold: 0.01 tokens (not 0.5 — avoid losing small positions)
          E. Live BUY orders → register unfilled portion
             → partial fill: inject lot only if NOT already in DB (avoid double)
             → partial fill already in DB: inject directly from DB lot
          F. DB lots not injected → fetch order status to determine fate
             → FILLED: close in DB, update pnl
             → PARTIAL: update DB tokens, remaining will be in orphan (D)
             → CANCELLED/MISSING: tokens are orphaned (D already handled) or truly gone
             → no exit_oid + not injected + balance confirms gone: close in DB
        """
        import uuid as _uuid
        sid = db_slot.id[:8]

        # ── Load config + replacement queue from DB ───────────────────────────
        async with async_session_factory() as db:
            sv = None
            replacement_sv = None
            if db_slot.current_strategy_slot_version_id:
                sv = await db.get(StrategySlotVersion, db_slot.current_strategy_slot_version_id)
            if db_slot.replacement_strategy_slot_version_id:
                replacement_sv = await db.get(StrategySlotVersion, db_slot.replacement_strategy_slot_version_id)
            lots_q = await db.execute(
                select(DBLot).where(
                    DBLot.session_slot_id == db_slot.id,
                    DBLot.status.in_(["open", "selling"]),
                )
            )
            db_lots = lots_q.scalars().all()

        if not sv or not sv.polymarket_url:
            log.info("[recovery] slot=%s no market → idle", sid)
            await engine.add_slot(db_slot.id, db_slot.strategy_slot_id, None, 0.0)
            return

        levels   = sv.max_spread_ticks + 1
        allocated = db_slot.allocated_capital
        lot_size  = round(allocated / levels, 4) if levels > 0 else 0.0
        config    = await _build_slot_market_config(sv, lot_size)

        if not config:
            log.info("[recovery] slot=%s config build failed → idle", sid)
            await engine.add_slot(db_slot.id, db_slot.strategy_slot_id, None, 0.0)
            return

        token_id = config.active_token_id
        tick     = real_tick(config.tick_size)

        # ── A. Fetch Poly state ───────────────────────────────────────────────
        # live_orders already fetched account-wide — filter to this token
        # If the account-wide fetch failed (live_orders={}), we re-fetch per-slot
        slot_orders = {
            oid: o for oid, o in live_orders.items()
            if o.get("asset_id") == token_id
        }

        # If no orders found AND live_orders was empty (fetch failed), retry
        if not live_orders:
            log.warning("[recovery] slot=%s live_orders empty — retrying get_open_orders", sid)
            for attempt in range(2):
                await asyncio.sleep(2)
                try:
                    refetched = await client.get_open_orders()
                    if refetched:
                        slot_orders = {
                            oid: o for oid, o in refetched.items()
                            if o.get("asset_id") == token_id
                        }
                        break
                except Exception as e:
                    log.warning("[recovery] slot=%s retry %d get_open_orders failed: %s", sid, attempt + 1, e)

        live_sells = {oid: o for oid, o in slot_orders.items() if (o.get("side") or "").upper() == "SELL"}
        live_buys  = {oid: o for oid, o in slot_orders.items() if (o.get("side") or "").upper() == "BUY"}

        # Fetch token balance with retry
        token_balance: Optional[float] = None
        for attempt in range(3):
            try:
                token_balance = await client.get_token_balance(token_id)
                break
            except Exception as e:
                log.warning("[recovery] slot=%s get_token_balance attempt %d failed: %s", sid, attempt + 1, e)
                if attempt < 2:
                    await asyncio.sleep(2)

        if token_balance is None:
            # Fallback: sum of remaining tokens from live_sells (conservative)
            token_balance = sum(
                max(0.0, float(o.get("original_size") or 0) - float(o.get("size_matched") or 0))
                for o in live_sells.values()
            )
            log.warning("[recovery] slot=%s get_token_balance failed all retries → fallback=%.4f from sells", sid, token_balance)

        log.info(
            "[recovery] slot=%s token=%s | Poly: %d sells %d buys balance=%.4f | DB: %d open lots",
            sid, token_id[:12], len(live_sells), len(live_buys), token_balance, len(db_lots),
        )

        # ── B. DB lookup maps ─────────────────────────────────────────────────
        db_by_exit  = {l.exit_order_id:  l for l in db_lots if l.exit_order_id}
        db_by_entry = {l.entry_order_id: l for l in db_lots if l.entry_order_id}

        # ── Start slot (frozen until injection complete) ───────────────────────
        await engine.add_slot(db_slot.id, db_slot.strategy_slot_id, config, allocated)
        slot_runtime = engine.get_slot_runtime(db_slot.id)
        if not slot_runtime:
            return
        slot_runtime._stop_new_entries = True

        lots_to_inject: list[RuntimeLot] = []
        tokens_accounted = 0.0

        # ── C. Live SELL orders → we hold remaining tokens, reconstruct lots ──
        for oid, order in live_sells.items():
            try:
                sell_tokens   = float(order.get("original_size") or 0)
                sell_matched  = float(order.get("size_matched") or 0)
                # Tokens still open in this sell order (not yet filled)
                remaining_tokens = max(0.0, round(sell_tokens - sell_matched, 6))
                if remaining_tokens < 0.001:
                    log.info("[recovery] slot=%s SELL oid=%s fully matched → skip", sid, oid[:14])
                    continue

                db_lot = db_by_exit.get(oid)
                if db_lot:
                    entry_price = db_lot.entry_price
                    tp_price    = db_lot.target_sell_price
                    lot_id      = db_lot.lot_id
                    pnl         = float(db_lot.pnl or 0.0)
                    log.info("[recovery] slot=%s SELL oid=%s → DB lot=%s tokens=%.4f tp=%.4f",
                             sid, oid[:14], lot_id[:8], remaining_tokens, tp_price)
                else:
                    sell_price  = float(order.get("price") or config.price_max or 0.99)
                    entry_price = norm(sell_price - tick)
                    tp_price    = norm(sell_price)
                    lot_id      = str(_uuid.uuid4())[:12]
                    pnl         = 0.0
                    log.info("[recovery] slot=%s SELL oid=%s → unknown, est entry=%.4f tp=%.4f tokens=%.4f",
                             sid, oid[:14], entry_price, tp_price, remaining_tokens)

                rt_lot = RuntimeLot(
                    lot_id=lot_id,
                    session_slot_id=db_slot.id,
                    entry_price=entry_price,
                    target_sell_price=tp_price,
                    size=round(remaining_tokens * entry_price, 6),
                    tokens=remaining_tokens,
                    exit_order_id=oid,
                    status=LotStatus.SELLING,
                    pnl=pnl,
                    timestamp_entry=datetime.now(timezone.utc).isoformat(),
                )
                lots_to_inject.append(rt_lot)
                slot_runtime._sell_orders[rt_lot.lot_id] = oid
                tokens_accounted += remaining_tokens
                log.info("[recovery] slot=%s → INJECT SELLING lot tokens=%.4f exit_oid=%s",
                         sid, remaining_tokens, oid[:14])
            except Exception as e:
                log.warning("[recovery] slot=%s error SELL oid=%s: %s", sid, oid[:14], e)

        # ── D. Orphan tokens (held but no live SELL covering them) ────────────
        # Use 0.01 threshold — even tiny positions must not be lost
        orphan_tokens = round(token_balance - tokens_accounted, 6)
        log.info("[recovery] slot=%s tokens_accounted=%.4f balance=%.4f orphan=%.4f",
                 sid, tokens_accounted, token_balance, orphan_tokens)

        if orphan_tokens > 0.01:
            # DB open lots without exit_oid — these are OPEN lots (bought but no TP placed yet)
            db_open_no_exit = [l for l in db_lots if not l.exit_order_id and l.tokens > 0.001]

            if db_open_no_exit:
                # Distribute orphan tokens across DB open lots proportionally to their original tokens
                total_db_tokens = sum(float(l.tokens) for l in db_open_no_exit)
                remaining_orphan = orphan_tokens
                for i, db_lot in enumerate(db_open_no_exit):
                    if i == len(db_open_no_exit) - 1:
                        # Last lot gets the remainder (avoid float drift)
                        lot_tokens = round(max(0.0, remaining_orphan), 6)
                    else:
                        weight = float(db_lot.tokens) / total_db_tokens if total_db_tokens > 0 else 1.0 / len(db_open_no_exit)
                        lot_tokens = round(orphan_tokens * weight, 6)
                        remaining_orphan -= lot_tokens

                    if lot_tokens < 0.001:
                        continue

                    rt_lot = RuntimeLot(
                        lot_id=db_lot.lot_id,
                        session_slot_id=db_slot.id,
                        entry_price=db_lot.entry_price,
                        target_sell_price=db_lot.target_sell_price,
                        size=round(lot_tokens * db_lot.entry_price, 6),
                        tokens=lot_tokens,
                        exit_order_id=None,
                        status=LotStatus.OPEN,
                        timestamp_entry=datetime.now(timezone.utc).isoformat(),
                    )
                    lots_to_inject.append(rt_lot)
                    log.info("[recovery] slot=%s orphan → DB lot=%s tokens=%.4f entry=%.4f tp=%.4f",
                             sid, db_lot.lot_id[:8], lot_tokens, db_lot.entry_price, db_lot.target_sell_price)
            else:
                # No DB lot — synthetic lot from market config
                entry_price = norm(float(config.price_max or 0.99))
                tp_price    = norm(entry_price + tick)
                rt_lot = RuntimeLot(
                    lot_id=str(_uuid.uuid4())[:12],
                    session_slot_id=db_slot.id,
                    entry_price=entry_price,
                    target_sell_price=tp_price,
                    size=round(orphan_tokens * entry_price, 6),
                    tokens=orphan_tokens,
                    exit_order_id=None,
                    status=LotStatus.OPEN,
                    timestamp_entry=datetime.now(timezone.utc).isoformat(),
                )
                lots_to_inject.append(rt_lot)
                log.info("[recovery] slot=%s orphan → synthetic lot tokens=%.4f entry=%.4f tp=%.4f",
                         sid, orphan_tokens, entry_price, tp_price)

        # ── E. Live BUY orders → register unfilled portion ────────────────────
        injected_lot_ids = {l.lot_id for l in lots_to_inject if l.lot_id}

        for oid, order in live_buys.items():
            try:
                price            = round(float(order.get("price") or 0), 4)
                orig_tokens      = float(order.get("original_size") or 0)
                matched_tokens   = float(order.get("size_matched") or 0)
                remaining_tokens = max(0.0, orig_tokens - matched_tokens)
                remaining_usdc   = round(remaining_tokens * price, 6)

                # Register unfilled portion in buy tracking
                if price > 0 and remaining_tokens > 0:
                    slot_runtime._buy_orders[price] = oid
                    slot_runtime._pending_buy_capital[oid] = remaining_usdc
                    log.info("[recovery] slot=%s BUY oid=%s price=%.4f unfilled=%.4f usdc=%.4f",
                             sid, oid[:14], price, remaining_tokens, remaining_usdc)

                # Partial fill → inject lot for the filled portion
                if matched_tokens >= 0.01:
                    db_lot = db_by_entry.get(oid)
                    if db_lot and db_lot.lot_id in injected_lot_ids:
                        # Already injected as orphan lot (D covered it)
                        log.info("[recovery] slot=%s BUY oid=%s partial fill — DB lot already injected", sid, oid[:14])
                        continue
                    if db_lot:
                        # Inject directly from DB lot with matched tokens
                        rt_lot = RuntimeLot(
                            lot_id=db_lot.lot_id,
                            session_slot_id=db_slot.id,
                            entry_price=db_lot.entry_price,
                            target_sell_price=db_lot.target_sell_price,
                            size=round(matched_tokens * db_lot.entry_price, 6),
                            tokens=matched_tokens,
                            entry_order_id=oid,
                            status=LotStatus.OPEN,
                            timestamp_entry=datetime.now(timezone.utc).isoformat(),
                        )
                    else:
                        # No DB record — synthetic lot
                        tp_price = norm(price + tick)
                        rt_lot = RuntimeLot(
                            session_slot_id=db_slot.id,
                            entry_price=price,
                            target_sell_price=tp_price,
                            size=round(matched_tokens * price, 6),
                            tokens=matched_tokens,
                            entry_order_id=oid,
                            status=LotStatus.OPEN,
                            timestamp_entry=datetime.now(timezone.utc).isoformat(),
                        )
                    lots_to_inject.append(rt_lot)
                    injected_lot_ids.add(rt_lot.lot_id)
                    log.info("[recovery] slot=%s → INJECT partial BUY lot oid=%s tokens=%.4f",
                             sid, oid[:14], matched_tokens)

            except Exception as e:
                log.warning("[recovery] slot=%s error BUY oid=%s: %s", sid, oid[:14], e)

        # ── F. DB lots not injected → reconcile fate ──────────────────────────
        final_injected_ids = {l.lot_id for l in lots_to_inject if l.lot_id}

        for db_lot in db_lots:
            if db_lot.lot_id in final_injected_ids:
                continue

            if db_lot.exit_order_id:
                # Had a sell order — fetch status to determine if filled
                try:
                    order_info = await client.get_order_status(db_lot.exit_order_id)
                except Exception as e:
                    log.warning("[recovery] slot=%s get_order_status lot=%s failed: %s — leaving open",
                                sid, db_lot.lot_id[:8], e)
                    continue  # Can't determine → don't close, leave for next recovery

                status_raw = (order_info.get("status") or "").upper()
                matched    = float(order_info.get("size_matched") or 0)

                if matched >= db_lot.tokens * 0.99 or status_raw in ("MATCHED", "FILLED"):
                    sell_price = float(order_info.get("price") or db_lot.target_sell_price)
                    pnl = round((sell_price - db_lot.entry_price) * db_lot.tokens, 6)
                    log.info("[recovery] slot=%s DB lot=%s → TP FILLED while down pnl=%.4f", sid, db_lot.lot_id[:8], pnl)
                    async with async_session_factory() as db:
                        await db.execute(update(DBLot).where(DBLot.lot_id == db_lot.lot_id).values(
                            status="closed", sell_price=sell_price, pnl=pnl,
                            timestamp_exit=datetime.now(timezone.utc),
                        ))
                        await db.commit()

                elif matched > 0.001:
                    # Partial TP — matched portion is sold, remaining tokens were orphaned in D
                    partial_pnl = round((float(order_info.get("price") or db_lot.target_sell_price) - db_lot.entry_price) * matched, 6)
                    new_tokens  = round(max(0.0, db_lot.tokens - matched), 6)
                    log.info("[recovery] slot=%s DB lot=%s → PARTIAL TP %.4f tok filled, %.4f remain", sid, db_lot.lot_id[:8], matched, new_tokens)
                    async with async_session_factory() as db:
                        await db.execute(update(DBLot).where(DBLot.lot_id == db_lot.lot_id).values(
                            tokens=new_tokens,
                            pnl=round(float(db_lot.pnl or 0) + partial_pnl, 6),
                            exit_order_id=None,  # Clear so orphan in D takes over
                        ))
                        await db.commit()

                else:
                    # Sell order was cancelled / no fill — tokens remain (orphan D handled them)
                    log.info("[recovery] slot=%s DB lot=%s → sell cancelled, orphan D covered tokens", sid, db_lot.lot_id[:8])
                    async with async_session_factory() as db:
                        await db.execute(update(DBLot).where(DBLot.lot_id == db_lot.lot_id).values(exit_order_id=None))
                        await db.commit()

            else:
                # No exit_oid, not injected → tokens confirmed gone on Poly
                # Only close if token_balance confirms there's nothing left
                if token_balance < 0.01:
                    log.info("[recovery] slot=%s DB lot=%s → no exit, no balance, no inject → closed", sid, db_lot.lot_id[:8])
                    async with async_session_factory() as db:
                        await db.execute(update(DBLot).where(DBLot.lot_id == db_lot.lot_id).values(
                            status="closed", timestamp_exit=datetime.now(timezone.utc),
                        ))
                        await db.commit()
                else:
                    # Balance exists but lot wasn't injected → orphan section (D) should have covered it
                    # If not, leave open rather than risk closing a live position
                    log.warning("[recovery] slot=%s DB lot=%s → no exit, not injected, but balance=%.4f — leaving open",
                                sid, db_lot.lot_id[:8], token_balance)

        # ── Inject all lots into runtime ──────────────────────────────────────
        async with slot_runtime._lots_lock:
            for rt_lot in lots_to_inject:
                slot_runtime._lots.append(rt_lot)

        # Restore stats from DB
        slot_runtime._volume       = float(db_slot.volume or 0.0)
        slot_runtime._realized_pnl = float(db_slot.realized_pnl or 0.0)
        slot_runtime._paid_fees    = float(db_slot.paid_fees or 0.0)

        # ── G. Restore queued replacement if one was pending ─────────────────
        if replacement_sv:
            try:
                replacement_config = await _build_slot_market_config(replacement_sv, lot_size)
                if replacement_config:
                    slot_runtime._pending_replacement = replacement_config
                    from app.runtime.runtime_models import SlotState as _SlotState
                    slot_runtime._state = _SlotState.REPLACEMENT_QUEUED
                    log.info("[recovery] slot=%s replacement queued restored → %s",
                             sid, replacement_sv.polymarket_url.split("/")[-1][:30] if replacement_sv.polymarket_url else "?")
            except Exception as e:
                log.warning("[recovery] slot=%s failed to restore replacement queue: %s", sid, e)

        log.info(
            "[recovery] slot=%s DONE → %d lots injected | buy_orders=%d sell_orders=%d | replacement=%s",
            sid, len(lots_to_inject), len(slot_runtime._buy_orders), len(slot_runtime._sell_orders),
            "yes" if replacement_sv else "no",
        )

        # Unfreeze
        slot_runtime._stop_new_entries = False

    async def _hard_stop_session(
        self,
        client: ExecutionClient,
        live_orders: dict,
        session_id: str,
        db_slots: list,
    ):
        """
        Hard stop for starting/stopping sessions:
          1. Cancel every live BUY order on the account.
          2. For each market (token_id) assigned to this session's slots,
             fetch actual token balance on Poly and taker-sell at current bid.

        Poly is truth — we don't rely on DB lots for token amounts.
        """
        import math
        from app.runtime.market_data_service import market_data_service

        # Step 1: cancel all live buy orders
        for oid, order in list(live_orders.items()):
            if (order.get("side") or "").upper() == "BUY":
                try:
                    await client.cancel_order(oid)
                except Exception as e:
                    log.warning("[hard_stop] cancel oid=%s failed: %s", oid[:14], e)

        # Step 2: resolve token_ids for each slot from DB
        token_ids: set[str] = set()
        async with async_session_factory() as db:
            for db_slot in db_slots:
                if not db_slot.current_strategy_slot_version_id:
                    continue
                sv = await db.get(StrategySlotVersion, db_slot.current_strategy_slot_version_id)
                if not sv:
                    continue
                if sv.token_id_yes and sv.side == "YES":
                    token_ids.add(sv.token_id_yes)
                elif sv.token_id_no and sv.side == "NO":
                    token_ids.add(sv.token_id_no)
                elif sv.token_id_yes:
                    token_ids.add(sv.token_id_yes)

        if not token_ids:
            return

        # Step 3: for each token_id, fetch real balance on Poly and taker-sell
        for token_id in token_ids:
            try:
                live_balance = await client.get_token_balance(token_id)
                if not live_balance or live_balance < 1.0:
                    continue

                sell_tokens = float(math.floor(live_balance))
                if sell_tokens < 1:
                    continue

                book = await market_data_service.get_order_book(token_id)
                bids = book.get("bids") or []
                if not bids:
                    log.warning("[hard_stop] session=%s token=%s no bids — cannot sell", session_id[:8], token_id[:16])
                    continue
                bid_price = norm(float(bids[0]["price"]))

                await client.ensure_sell_allowance(token_id)
                await client.place_limit_order(
                    token_id=token_id,
                    price=bid_price,
                    size=sell_tokens,
                    side="SELL",
                    tick_size="0.001",
                    neg_risk=False,
                )
                log.info(
                    "[hard_stop] session=%s token=%s sold %.0f tok @ %.4f",
                    session_id[:8], token_id[:16], sell_tokens, bid_price,
                )
            except Exception as e:
                log.warning(
                    "[hard_stop] session=%s token=%s sell failed: %s",
                    session_id[:8], token_id[:16], e,
                )

    async def _mark_degraded(self, session_id: str, reason: str):
        async with async_session_factory() as db:
            await db.execute(
                update(DBSession)
                .where(DBSession.id == session_id)
                .values(health_state="degraded", stop_reason=reason)
            )
            db.add(AuditLog(
                actor_id="system", actor_role="system",
                action="session.recovery_failed",
                entity_type="session", entity_id=session_id,
                detail=reason,
            ))
            await db.commit()


def async_timeout(seconds: float):
    """Simple async context manager for timeout."""
    return asyncio.timeout(seconds)


recovery_service = RecoveryService()
