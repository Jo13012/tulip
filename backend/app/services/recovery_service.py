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
          B. Build DB lookup maps: exit_oid→lot, entry_oid→lot
          C. For each live SELL order → we hold those tokens, reconstruct the lot
          D. Tokens held but not covered by any live SELL → orphan tokens, post TP
          E. For each live BUY order → register in buy_orders tracking
             If partially filled → create lot for the filled portion
          F. Close DB lots that are no longer reflected in Poly state
        """
        sid = db_slot.id[:8]

        # ── Load config ───────────────────────────────────────────────────────
        async with async_session_factory() as db:
            sv = None
            if db_slot.current_strategy_slot_version_id:
                sv = await db.get(StrategySlotVersion, db_slot.current_strategy_slot_version_id)
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
        # Filter account-wide live_orders to this slot's token only
        slot_orders = {
            oid: o for oid, o in live_orders.items()
            if o.get("asset_id") == token_id
        }
        live_sells = {oid: o for oid, o in slot_orders.items() if (o.get("side") or "").upper() == "SELL"}
        live_buys  = {oid: o for oid, o in slot_orders.items() if (o.get("side") or "").upper() == "BUY"}

        token_balance = 0.0
        try:
            token_balance = (await client.get_token_balance(token_id)) or 0.0
        except Exception as e:
            log.warning("[recovery] slot=%s get_token_balance failed: %s", sid, e)

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

        # ── C. Live SELL orders → we hold tokens, reconstruct lots ───────────
        for oid, order in live_sells.items():
            try:
                sell_tokens = float(order.get("original_size") or 0)
                sell_matched = float(order.get("size_matched") or 0)
                remaining_tokens = max(0.0, sell_tokens - sell_matched)
                if remaining_tokens < 0.001:
                    log.info("[recovery] slot=%s SELL oid=%s fully matched → skip", sid, oid[:14])
                    continue

                # Enrich from DB if we know this sell order
                db_lot = db_by_exit.get(oid)
                if db_lot:
                    entry_price = db_lot.entry_price
                    tp_price    = db_lot.target_sell_price
                    lot_id      = db_lot.lot_id
                    pnl         = db_lot.pnl or 0.0
                    log.info("[recovery] slot=%s SELL oid=%s → matched DB lot=%s tokens=%.4f tp=%.4f",
                             sid, oid[:14], lot_id[:8], remaining_tokens, tp_price)
                else:
                    # Unknown sell — estimate entry from sell price - 1 tick
                    sell_price  = float(order.get("price") or config.price_max or 0.99)
                    entry_price = norm(sell_price - tick)
                    tp_price    = norm(sell_price)
                    lot_id      = None
                    pnl         = 0.0
                    log.info("[recovery] slot=%s SELL oid=%s → unknown in DB, est entry=%.4f tp=%.4f tokens=%.4f",
                             sid, oid[:14], entry_price, tp_price, remaining_tokens)

                size = round(remaining_tokens * entry_price, 6)
                rt_lot = RuntimeLot(
                    lot_id=lot_id,
                    session_slot_id=db_slot.id,
                    entry_price=entry_price,
                    target_sell_price=tp_price,
                    size=size,
                    tokens=remaining_tokens,
                    exit_order_id=oid,
                    status=LotStatus.SELLING,
                    pnl=pnl,
                    timestamp_entry=datetime.now(timezone.utc).isoformat(),
                )
                lots_to_inject.append(rt_lot)
                slot_runtime._sell_orders[rt_lot.lot_id] = oid
                tokens_accounted += remaining_tokens
                log.info("[recovery] slot=%s → INJECT lot (SELLING) tokens=%.4f exit_oid=%s",
                         sid, remaining_tokens, oid[:14])
            except Exception as e:
                log.warning("[recovery] slot=%s error processing SELL oid=%s: %s", sid, oid[:14], e)

        # ── D. Orphan tokens (held but no live SELL covering them) ────────────
        orphan_tokens = round(token_balance - tokens_accounted, 4)
        log.info("[recovery] slot=%s tokens_accounted=%.4f balance=%.4f orphan=%.4f",
                 sid, tokens_accounted, token_balance, orphan_tokens)

        if orphan_tokens > 0.5:
            # Find a DB lot that is open (no exit_oid) to get correct entry price
            db_open_no_exit = [l for l in db_lots if not l.exit_order_id and l.tokens > 0.001]
            if db_open_no_exit:
                # Use DB lot details, just update tokens to match reality
                db_lot = db_open_no_exit[0]
                entry_price = db_lot.entry_price
                tp_price    = db_lot.target_sell_price
                lot_id      = db_lot.lot_id
                log.info("[recovery] slot=%s orphan → matched DB open lot=%s entry=%.4f tp=%.4f",
                         sid, lot_id[:8], entry_price, tp_price)
            else:
                # No DB lot — estimate from market config
                entry_price = config.price_max or 0.99
                tp_price    = norm(entry_price + tick)
                lot_id      = None
                log.info("[recovery] slot=%s orphan → no DB lot, est entry=%.4f tp=%.4f",
                         sid, entry_price, tp_price)

            rt_lot = RuntimeLot(
                lot_id=lot_id,
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
            log.info("[recovery] slot=%s → INJECT orphan lot tokens=%.4f tp=%.4f", sid, orphan_tokens, tp_price)

        # ── E. Live BUY orders → register in tracking ─────────────────────────
        for oid, order in live_buys.items():
            try:
                price         = round(float(order.get("price") or 0), 4)
                orig_tokens   = float(order.get("original_size") or 0)
                matched_tokens = float(order.get("size_matched") or 0)
                remaining_tokens = max(0.0, orig_tokens - matched_tokens)
                remaining_usdc   = round(remaining_tokens * price, 6)

                # Register unfilled portion in buy tracking
                slot_runtime._buy_orders[price] = oid
                slot_runtime._pending_buy_capital[oid] = remaining_usdc
                log.info("[recovery] slot=%s BUY oid=%s price=%.4f orig=%.4f matched=%.4f remaining=%.4f",
                         sid, oid[:14], price, orig_tokens, matched_tokens, remaining_tokens)

                # Partial fill → create lot for the already-filled portion
                if matched_tokens >= 0.01:
                    # Check if we already have a lot for this entry_oid
                    if oid in db_by_entry:
                        log.info("[recovery] slot=%s BUY oid=%s partial fill already in DB lot=%s",
                                 sid, oid[:14], db_by_entry[oid].lot_id[:8])
                        # Already handled via DB lot path — don't double-create
                        continue
                    # Not in DB → create lot for the matched portion
                    tp_price  = norm(price + tick)
                    usdc_cost = round(matched_tokens * price, 6)
                    rt_lot = RuntimeLot(
                        session_slot_id=db_slot.id,
                        entry_price=price,
                        target_sell_price=tp_price,
                        size=usdc_cost,
                        tokens=matched_tokens,
                        entry_order_id=oid,
                        status=LotStatus.OPEN,
                        timestamp_entry=datetime.now(timezone.utc).isoformat(),
                    )
                    lots_to_inject.append(rt_lot)
                    log.info("[recovery] slot=%s → INJECT partial BUY lot oid=%s tokens=%.4f",
                             sid, oid[:14], matched_tokens)

            except Exception as e:
                log.warning("[recovery] slot=%s error processing BUY oid=%s: %s", sid, oid[:14], e)

        # ── F. Close DB lots no longer in Poly state ──────────────────────────
        # Any DB lot whose exit_oid is not in live_sells and whose tokens are
        # not accounted for in our injected lots = was sold while we were down.
        injected_lot_ids = {l.lot_id for l in lots_to_inject if l.lot_id}
        for db_lot in db_lots:
            if db_lot.lot_id in injected_lot_ids:
                continue
            # Not injected → Poly no longer has this position
            if db_lot.exit_order_id:
                # Had a sell order → check if it filled
                order_info   = await client.get_order_status(db_lot.exit_order_id)
                status_raw   = (order_info.get("status") or "").upper()
                matched      = float(order_info.get("size_matched") or 0)
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
                elif matched > 0.01:
                    # Partial TP fill — remaining tokens should have been caught as orphan above
                    partial_pnl = round((db_lot.target_sell_price - db_lot.entry_price) * matched, 6)
                    log.info("[recovery] slot=%s DB lot=%s → PARTIAL TP %.4f tok filled while down", sid, db_lot.lot_id[:8], matched)
                    async with async_session_factory() as db:
                        await db.execute(update(DBLot).where(DBLot.lot_id == db_lot.lot_id).values(
                            tokens=max(0.0, round(db_lot.tokens - matched, 6)),
                            pnl=round(db_lot.pnl + partial_pnl, 6),
                            exit_order_id=None,
                        ))
                        await db.commit()
                else:
                    # Sell order cancelled, tokens still held (should be in orphan)
                    log.info("[recovery] slot=%s DB lot=%s → sell cancelled, tokens remain (orphan covers)", sid, db_lot.lot_id[:8])
                    async with async_session_factory() as db:
                        await db.execute(update(DBLot).where(DBLot.lot_id == db_lot.lot_id).values(exit_order_id=None))
                        await db.commit()
            else:
                # No exit_oid and not injected — tokens are gone (already sold or market resolved)
                log.info("[recovery] slot=%s DB lot=%s → no exit_oid not injected → mark closed (tokens gone)", sid, db_lot.lot_id[:8])
                async with async_session_factory() as db:
                    await db.execute(update(DBLot).where(DBLot.lot_id == db_lot.lot_id).values(
                        status="closed", timestamp_exit=datetime.now(timezone.utc),
                    ))
                    await db.commit()

        # ── Inject all lots into runtime ──────────────────────────────────────
        async with slot_runtime._lots_lock:
            for rt_lot in lots_to_inject:
                slot_runtime._lots.append(rt_lot)

        # Restore stats from DB
        slot_runtime._volume       = float(db_slot.volume or 0.0)
        slot_runtime._realized_pnl = float(db_slot.realized_pnl or 0.0)
        slot_runtime._paid_fees    = float(db_slot.paid_fees or 0.0)

        log.info(
            "[recovery] slot=%s DONE → %d lots injected | buy_orders=%d sell_orders=%d",
            sid, len(lots_to_inject), len(slot_runtime._buy_orders), len(slot_runtime._sell_orders),
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
