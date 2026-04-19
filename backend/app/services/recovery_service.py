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

        # Restore original started_at so uptime is correct after recovery
        if session.started_at:
            engine.started_at = session.started_at

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
        """Reconstruct slot runtime from DB + live exchange state.

        Reconciliation handles all events that may have occurred during downtime:
          - BUY maker filled while down → create lot, post TP
          - TP SELL filled while down  → close lot in DB, skip injection
          - BUY order gone from Poly   → discard from tracking (no capital lock)
          - Partial TP fill while down → adjust tokens/size before injecting
          - 'selling' lot already sold → phantom-close and skip injection
        """
        async with async_session_factory() as db:
            sv = None
            if db_slot.current_strategy_slot_version_id:
                sv = await db.get(StrategySlotVersion, db_slot.current_strategy_slot_version_id)

            # Load open/selling lots from DB
            lots_q = await db.execute(
                select(DBLot).where(
                    DBLot.session_slot_id == db_slot.id,
                    DBLot.status.in_(["open", "selling"]),
                )
            )
            db_lots = lots_q.scalars().all()

        if not sv or not sv.polymarket_url:
            await engine.add_slot(db_slot.id, db_slot.strategy_slot_id, None, 0.0)
            return

        levels = sv.max_spread_ticks + 1
        allocated = db_slot.allocated_capital
        lot_size = round(allocated / levels, 4) if levels > 0 else 0.0
        config = await _build_slot_market_config(sv, lot_size)

        if not config:
            await engine.add_slot(db_slot.id, db_slot.strategy_slot_id, None, 0.0)
            return

        # Add slot (starts market loop)
        await engine.add_slot(db_slot.id, db_slot.strategy_slot_id, config, allocated)

        slot_runtime = engine.get_slot_runtime(db_slot.id)
        if not slot_runtime:
            return

        # ── Step 1: Reconcile DB lots against live exchange state ─────────────
        # live_orders keyed by order_id: includes all open orders on Poly right now.
        # Any lot whose exit_order_id is NOT in live_orders may have been filled.
        # Any lot whose entry_order_id is NOT in live_orders may have been filled.

        # Build a set of live sell order ids for quick lookup
        live_sell_oids: set[str] = {
            oid for oid, o in live_orders.items()
            if (o.get("side") or "").upper() == "SELL"
        }

        lots_to_inject: list[RuntimeLot] = []

        for db_lot in db_lots:
            exit_oid  = db_lot.exit_order_id
            entry_oid = db_lot.entry_order_id

            # ── Case A: lot has a sell order that's no longer live ─────────────
            # Could be: filled, phantom-filled, or just never placed properly.
            if exit_oid and exit_oid not in live_sell_oids:
                # Check actual order status to distinguish filled vs cancelled
                order_info = await client.get_order_status(exit_oid)
                status_raw = (order_info.get("status") or "").upper()
                tokens_matched = 0.0
                try:
                    tokens_matched = float(
                        order_info.get("size_matched") or order_info.get("matched_amount") or 0
                    )
                except (TypeError, ValueError):
                    pass

                if tokens_matched >= db_lot.tokens * 0.99 or status_raw in ("MATCHED", "FILLED"):
                    # TP sell was filled while down → close lot in DB, skip injection
                    actual_sell = db_lot.target_sell_price
                    pnl = round((actual_sell - db_lot.entry_price) * db_lot.tokens, 6)
                    async with async_session_factory() as db:
                        await db.execute(
                            update(DBLot)
                            .where(DBLot.lot_id == db_lot.lot_id)
                            .values(
                                status="closed",
                                sell_price=actual_sell,
                                pnl=pnl,
                                timestamp_exit=datetime.now(timezone.utc),
                            )
                        )
                        await db.commit()
                    log.info(
                        "[recovery] slot=%s lot=%s TP filled while down → closed (pnl=%.4f)",
                        db_slot.id[:8], db_lot.lot_id[:8], pnl,
                    )
                    continue  # don't inject

                elif tokens_matched > 0.01:
                    # Partial fill → adjust tokens before injecting
                    remaining_tokens = max(0.0, round(db_lot.tokens - tokens_matched, 6))
                    remaining_size   = round(remaining_tokens * db_lot.entry_price, 6)
                    partial_pnl      = round((db_lot.target_sell_price - db_lot.entry_price) * tokens_matched, 6)
                    async with async_session_factory() as db:
                        await db.execute(
                            update(DBLot)
                            .where(DBLot.lot_id == db_lot.lot_id)
                            .values(
                                tokens=remaining_tokens,
                                size_usdc=remaining_size,
                                pnl=round(db_lot.pnl + partial_pnl, 6),
                                exit_order_id=None,  # sell order is gone, will repost
                            )
                        )
                        await db.commit()
                    # Rebuild db_lot fields for injection
                    db_lot.tokens      = remaining_tokens
                    db_lot.size_usdc   = remaining_size
                    db_lot.exit_order_id = None
                    log.info(
                        "[recovery] slot=%s lot=%s partial TP fill while down → tokens=%.4f remaining",
                        db_slot.id[:8], db_lot.lot_id[:8], remaining_tokens,
                    )

                else:
                    # Sell order cancelled/gone with no fill → clear exit_order_id, will repost TP
                    async with async_session_factory() as db:
                        await db.execute(
                            update(DBLot)
                            .where(DBLot.lot_id == db_lot.lot_id)
                            .values(exit_order_id=None)
                        )
                        await db.commit()
                    db_lot.exit_order_id = None

            # ── Case B: 'selling' lot with no exit_order_id ────────────────────
            # Status was 'selling' when we went down but sell hadn't been posted yet,
            # or phantom case. Check real token balance to decide.
            elif db_lot.status == "selling" and not exit_oid:
                live_tokens = await client.get_token_balance(config.active_token_id)
                if live_tokens is not None and live_tokens < db_lot.tokens * 0.5:
                    # Tokens are gone — phantom close
                    pnl = round((db_lot.target_sell_price - db_lot.entry_price) * db_lot.tokens, 6)
                    async with async_session_factory() as db:
                        await db.execute(
                            update(DBLot)
                            .where(DBLot.lot_id == db_lot.lot_id)
                            .values(
                                status="closed",
                                sell_price=db_lot.target_sell_price,
                                pnl=pnl,
                                timestamp_exit=datetime.now(timezone.utc),
                            )
                        )
                        await db.commit()
                    log.info(
                        "[recovery] slot=%s lot=%s phantom close (tokens gone) pnl=%.4f",
                        db_slot.id[:8], db_lot.lot_id[:8], pnl,
                    )
                    continue

            # ── Lot survived reconciliation → inject into runtime ──────────────
            if db_lot.tokens < 0.001:
                # Sub-dust lot, skip
                continue

            rt_lot = RuntimeLot(
                lot_id=db_lot.lot_id,
                session_slot_id=db_slot.id,
                entry_price=db_lot.entry_price,
                target_sell_price=db_lot.target_sell_price,
                size=db_lot.size_usdc,
                tokens=db_lot.tokens,
                entry_order_id=db_lot.entry_order_id,
                exit_order_id=db_lot.exit_order_id,
                status=LotStatus.OPEN,  # normalize: recovering lots are either open or will repost
                pnl=db_lot.pnl,
                sell_price=db_lot.sell_price,
            )
            lots_to_inject.append(rt_lot)

        # ── Step 2: Detect BUY orders filled while down → create new lots ─────
        # These are buys that were live, got filled, but no lot was ever written to DB.
        known_entry_oids: set[str] = {
            dl.entry_order_id for dl in db_lots if dl.entry_order_id
        }
        # Also include injected lots so we don't double-create
        known_entry_oids |= {l.entry_order_id for l in lots_to_inject if l.entry_order_id}

        for oid, order in live_orders.items():
            # Only BUY orders not already tracked by a DB lot
            if (order.get("side") or "").upper() != "BUY":
                continue
            if oid in known_entry_oids:
                continue

            # This buy order is live on Poly but has no matching lot in DB
            # It was in-flight when we crashed — check fill status
            try:
                price_str = order.get("price")
                if not price_str:
                    continue
                price = round(float(price_str), 4)
                matched_str = order.get("size_matched") or order.get("matched_amount") or "0"
                matched_tokens = float(matched_str)

                if matched_tokens >= 0.01:
                    # Partial or full fill happened while down → synthesize lot(s)
                    tick = real_tick(config.tick_size)
                    tp_price = norm(price + tick)
                    usdc_cost = round(matched_tokens * price, 6)

                    # Check if fully matched (no longer live)
                    status_raw = (order.get("status") or "").upper()
                    is_fully_filled = status_raw in ("MATCHED", "FILLED")

                    lot = RuntimeLot(
                        session_slot_id=db_slot.id,
                        entry_price=price,
                        target_sell_price=tp_price,
                        size=usdc_cost,
                        tokens=matched_tokens,
                        entry_order_id=oid,
                        status=LotStatus.OPEN,
                        timestamp_entry=datetime.now(timezone.utc).isoformat(),
                    )
                    lots_to_inject.append(lot)
                    log.info(
                        "[recovery] slot=%s new lot from filled BUY oid=%s tokens=%.4f price=%.4f",
                        db_slot.id[:8], oid[:14], matched_tokens, price,
                    )

                    if not is_fully_filled:
                        # Still live (partial) — register in buy_orders tracking
                        remaining_size = round(
                            (float(order.get("original_size") or matched_tokens) - matched_tokens) * price, 6
                        )
                        slot_runtime._buy_orders[price] = oid
                        slot_runtime._pending_buy_capital[oid] = max(0.0, remaining_size)
                else:
                    # Still live, unfilled → register in buy_orders as normal
                    orig_size_str = order.get("original_size") or order.get("size") or "0"
                    try:
                        size_usdc = float(orig_size_str) * price
                    except (ValueError, TypeError):
                        size_usdc = 0.0
                    slot_runtime._buy_orders[price] = oid
                    slot_runtime._pending_buy_capital[oid] = size_usdc

            except Exception as e:
                log.warning("[recovery] slot=%s error reconciling buy oid=%s: %s", db_slot.id[:8], oid[:14], e)

        # ── Step 3: Inject reconciled lots + restore sell order tracking ───────
        async with slot_runtime._lots_lock:
            for rt_lot in lots_to_inject:
                slot_runtime._lots.append(rt_lot)
                # If lot had an active sell order that's still live, restore tracking
                if rt_lot.exit_order_id and rt_lot.exit_order_id in live_sell_oids:
                    slot_runtime._sell_orders[rt_lot.lot_id] = rt_lot.exit_order_id

        # ── Step 4: Register remaining live BUY orders not yet in tracking ────
        # (for orders that had no fills and aren't already registered from step 2)
        for oid, order in live_orders.items():
            if (order.get("side") or "").upper() != "BUY":
                continue
            price_str = order.get("price")
            if not price_str:
                continue
            try:
                price = round(float(price_str), 4)
            except (ValueError, TypeError):
                continue
            if price in slot_runtime._buy_orders:
                continue  # already registered
            orig_size_str = order.get("original_size") or order.get("size") or "0"
            matched_str   = order.get("size_matched") or "0"
            try:
                orig_tokens  = float(orig_size_str)
                matched_tok  = float(matched_str)
                remaining    = max(0.0, orig_tokens - matched_tok)
                size_usdc    = round(remaining * price, 6)
            except (ValueError, TypeError):
                size_usdc = 0.0
            slot_runtime._buy_orders[price] = oid
            slot_runtime._pending_buy_capital[oid] = size_usdc

        # Re-enable entries (will be overridden by soft_stop() if session was stopping)
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
