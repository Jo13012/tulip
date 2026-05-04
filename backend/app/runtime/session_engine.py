"""
session_engine.py — Per-session engine.

Manages all slot runtimes for one user session.
Handles:
  - slot lifecycle (start/stop/replace)
  - session-level SL monitoring
  - stop mode enforcement (days/volume)
  - global pause propagation
  - soft/hard stop
  - fee reserve tracking
  - WS broadcast notifications
"""
from __future__ import annotations
import asyncio
import time
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable

from sqlalchemy import update as sa_update

from app.runtime.runtime_models import (
    RuntimeLot, SlotMarketConfig, SlotState, SessionHealthState,
    SessionRuntimeSummary, StopMode, norm,
)
from app.runtime.session_slot_runtime import SessionSlotRuntime
from app.integrations.polymarket.execution_client import ExecutionClient
from app.services.fee_service import fee_service
from app.services.nuclear_close_service import run_nuclear_close
from app.core.config import settings

# XP level thresholds and corresponding fee_bps (basis points = fee% * 100 * 10)
# Level 1: 5 bps = 0.05%; Level 10: 1 bps = 0.01%
_XP_VOLUME_THRESHOLDS = [0, 25_000, 75_000, 150_000, 300_000, 500_000, 750_000, 1_000_000, 1_500_000, 2_000_000]
_XP_FEE_BPS           = [120, 110, 100, 90, 80, 70, 60, 50, 40, 30]


def _compute_xp_fee_bps(trading_volume: float) -> int:
    """Return effective_fee_bps from XP table based on lifetime trading_volume."""
    level = 0
    for i, threshold in enumerate(_XP_VOLUME_THRESHOLDS):
        if trading_volume >= threshold:
            level = i
        else:
            break
    return _XP_FEE_BPS[level]


class SessionEngine:
    """
    Per-session engine. One instance per active session.
    Owns all slot runtimes for this session.
    """

    SNAPSHOT_INTERVAL = 300.0  # 5 min periodic snapshot

    def __init__(
        self,
        session_id: str,
        user_id: str,
        wallet_id: str,
        execution_client: ExecutionClient,
        gross_capital: float,
        deployable_capital: float,
        fee_reserve: float,
        stop_mode: StopMode,
        stop_days: Optional[int],
        target_volume: Optional[float],
        session_sl_pct: float,
        strategy=None,
        privy_wallet_id: str = "",
        fee_wallet_address: str = "",
        referrer_user_id: str = "",
        referrer_wallet_address: str = "",
        user_fee_bps: int = 120,                      # active fee at session start (override ?? XP)
        fee_is_overridden: bool = False,             # True when fee_override_bps is set; suppresses XP live-update
        user_trading_volume: float = 0.0,            # lifetime trading volume at session start
        free_fee_ends_at: Optional[datetime] = None, # set at onboarding; monitor clears override when passed
        db_factory=None,                             # async callable returning AsyncSession context manager
        on_broadcast: Optional[Callable[[], Awaitable[None]]] = None,
        on_snapshot: Optional[Callable[[dict], Awaitable[None]]] = None,
        on_lot_event: Optional[Callable[[str, RuntimeLot, Optional[float]], Awaitable[None]]] = None,
        on_log: Optional[Callable[[str, str, str], Awaitable[None]]] = None,
        # (level, message, session_slot_id)
    ):
        self.session_id          = session_id
        self.user_id             = user_id
        self.wallet_id           = wallet_id
        self._client             = execution_client

        self.gross_capital       = gross_capital
        self.deployable_capital  = deployable_capital
        self.fee_reserve         = fee_reserve
        self.fee_reserve_used    = 0.0

        self.stop_mode           = stop_mode
        self.stop_days           = stop_days
        self.target_volume       = target_volume
        self.session_sl_pct      = session_sl_pct
        self._strategy           = strategy
        self._privy_wallet_id    = privy_wallet_id
        self._fee_wallet_address = fee_wallet_address
        self._referrer_user_id = referrer_user_id
        self._referrer_wallet_address = referrer_wallet_address

        # Per-user fee
        # _user_fee_bps = active fee used for all fills (override or XP-computed)
        # _fee_is_overridden = True if admin override is active; XP updates are still
        #   written to effective_fee_bps in DB but do NOT change _user_fee_bps.
        self._user_fee_bps        = user_fee_bps
        self._fee_is_overridden   = fee_is_overridden
        self._user_trading_volume = user_trading_volume
        self._free_fee_ends_at    = free_fee_ends_at      # None = manual override, never auto-expire
        self._db_factory          = db_factory

        self._on_broadcast       = on_broadcast
        self._on_snapshot        = on_snapshot
        self._on_lot_event       = on_lot_event
        self._on_log             = on_log

        self.health_state        = SessionHealthState.STARTING
        self.status              = "starting"
        self.stop_reason: Optional[str] = None
        self.started_at: Optional[datetime] = None

        # slot runtimes: {session_slot_id: SessionSlotRuntime}
        self._slot_runtimes: dict[str, SessionSlotRuntime] = {}

        self._running            = False
        self._start_time         = 0.0
        self._monitor_task: Optional[asyncio.Task] = None
        self._snapshot_task: Optional[asyncio.Task] = None
        self._last_arb_balance_check = 0.0  # monotonic timestamp of last Arbitrum balance RPC

        # In-memory session log (ring buffer, 5000 entries)
        self._log_buffer: list[dict] = []
        self._log_max = 5000
        # Dedup: track last message per (level, session_slot_id) to collapse repeats
        self._log_last: dict[str, tuple[str, int]] = {}  # key → (msg, count)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self):
        if self._running:
            return
        self._running    = True
        self._start_time = time.monotonic()
        self.started_at  = datetime.now(timezone.utc)
        self.status      = "running"
        self.health_state = SessionHealthState.RUNNING
        self._monitor_task  = asyncio.create_task(self._monitor_loop(), name=f"mon_{self.session_id[:8]}")
        self._snapshot_task = asyncio.create_task(self._snapshot_loop(), name=f"snap_{self.session_id[:8]}")
        await self._log_session("SUCCESS", "Session engine started")

    async def stop(self, reason: str = "manual"):
        self._running = False
        self.stop_reason = reason
        self.status = "stopped"
        self.health_state = SessionHealthState.STOPPED
        if self._monitor_task:
            self._monitor_task.cancel()
        if self._snapshot_task:
            self._snapshot_task.cancel()
        for slot in self._slot_runtimes.values():
            await slot.stop(reason=reason)
        await self._log_session("INFO", f"Session stopped: {reason}")
        await self._broadcast()
        try:
            from app.runtime.session_registry import session_registry
            await session_registry.remove(self.session_id)
        except Exception:
            pass

    async def soft_stop(self):
        """Stop new entries across all slots; let positions exit cleanly."""
        if self.health_state == SessionHealthState.STOPPING:
            return  # already stopping — don't spam
        for slot in self._slot_runtimes.values():
            await slot.soft_stop()
        self.status       = "stopping"
        self.health_state = SessionHealthState.STOPPING
        await self._log_session("INFO", "Soft stop — no new entries")
        # Persist stopping state to DB so admin view is accurate
        if self._db_factory:
            try:
                from app.db.models import Session as DBSession
                async with self._db_factory() as db:
                    await db.execute(
                        sa_update(DBSession)
                        .where(DBSession.id == self.session_id)
                        .values(status="stopping", health_state="stopping")
                    )
                    await db.commit()
            except Exception:
                pass

    async def _write_final_snapshot(self) -> None:
        """Persist final session stats as a SessionSnapshot row."""
        if not self._db_factory:
            return
        try:
            import uuid
            from app.db.models import SessionSnapshot
            s = self.summary
            snap = SessionSnapshot(
                id=str(uuid.uuid4()),
                session_id=self.session_id,
                gross_capital=s.gross_capital,
                deployable_capital=self.deployable_capital,
                fee_reserve_remaining=max(0.0, self.fee_reserve - self.fee_reserve_used),
                deployed_capital=s.total_deployed,
                total_volume=s.total_volume,
                realized_pnl=s.realized_pnl,
                unrealized_pnl=s.unrealized_pnl,
                paid_fees=s.paid_fees,
                health_state="stopped",
            )
            async with self._db_factory() as db:
                db.add(snap)
                await db.commit()
        except Exception as e:
            await self._log_session("ERROR", f"Failed to write final snapshot: {e}")

    async def hard_stop(self, reason: str = "hard stop"):
        """Stop local runtimes, then nuclear-close positions directly on Polymarket."""
        await self._log_session("INFO", f"Hard stop requested: {reason}")
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
        if self._snapshot_task:
            self._snapshot_task.cancel()
        for slot in self._slot_runtimes.values():
            await slot.stop(reason="hard stop")
        try:
            close_result = await run_nuclear_close(
                self._client,
                cancel_orders=True,
                sell_all_positions=True,
            )
            await self._log_session(
                "INFO",
                f"Hard stop nuclear close: cancelled={len(close_result['cancelled_orders'])} sold={len(close_result['sold_positions'])} redeemed={len(close_result.get('redeemed_positions', []))} errors={len(close_result['errors'])}",
            )
        except Exception as e:
            await self._log_session("ERROR", f"Hard stop nuclear close failed: {e}")
        self.stop_reason  = reason
        self.status       = "stopped"
        self.health_state = SessionHealthState.STOPPED
        await self._write_final_snapshot()
        if self._db_factory:
            try:
                from app.db.models import Session as DBSession
                async with self._db_factory() as db:
                    await db.execute(
                        sa_update(DBSession)
                        .where(DBSession.id == self.session_id)
                        .values(
                            status="stopped",
                            health_state="stopped",
                            stopped_at=datetime.now(timezone.utc),
                            stop_reason=reason,
                        )
                    )
                    await db.commit()
            except Exception as e:
                await self._log_session("ERROR", f"Failed to persist hard stop: {e}")
        await self._log_session("WARN", f"Hard stop: {reason}")

    # ── Slot management ───────────────────────────────────────────────────────

    async def add_slot(
        self,
        session_slot_id: str,
        strategy_slot_id: str,
        config: Optional[SlotMarketConfig],
        allocated_capital: float,
    ):
        """Add and optionally start a slot runtime."""
        slot = SessionSlotRuntime(
            session_slot_id=session_slot_id,
            session_id=self.session_id,
            execution_client=self._client,
            on_lot_opened=lambda lot: self._on_lot_opened(session_slot_id, lot),
            on_lot_closed=lambda lot, pnl: self._on_lot_closed(session_slot_id, lot, pnl),
            on_state_change=lambda state, reason: self._on_slot_state(session_slot_id, state, reason),
            on_log=lambda level, msg: self._on_slot_log(session_slot_id, level, msg),
        )
        self._slot_runtimes[session_slot_id] = slot

        if config and config.active_token_id and allocated_capital > 0:
            await slot.start(config, allocated_capital)
        else:
            await self._log_session("INFO", f"Slot {session_slot_id[:8]} added (idle — no market)")

    async def queue_slot_replacement(self, session_slot_id: str, new_config: Optional[SlotMarketConfig]):
        slot = self._slot_runtimes.get(session_slot_id)
        if not slot:
            return
        await slot.queue_replacement(new_config)
        await self._broadcast()

    async def force_replace_slot(self, session_slot_id: str, new_config: Optional[SlotMarketConfig]):
        slot = self._slot_runtimes.get(session_slot_id)
        if not slot:
            return
        await slot.force_replace(new_config)
        await self._broadcast()

    async def update_slot_capital(self, session_slot_id: str, capital: float):
        slot = self._slot_runtimes.get(session_slot_id)
        if slot:
            slot.set_allocated_capital(capital)

    # ── Session monitoring ────────────────────────────────────────────────────

    async def _monitor_loop(self):
        while self._running:
            try:
                await self._check_free_fee_expiry()
                await self._check_session_sl()
                await self._check_stop_mode()
                await self._check_arbitrum_fee_buffer()
                await self._check_soft_stop_complete()
                await self._broadcast()
            except Exception as e:
                await self._log_session("ERROR", f"Monitor error: {e}")
            await asyncio.sleep(5.0)

    async def _check_free_fee_expiry(self):
        """
        Auto free-fee expiry — pure in-memory check, zero DB queries per tick.
        Only fires when override=0 AND free_fee_ends_at is set (auto free-fee, not manual).
        """
        if not self._fee_is_overridden or self._user_fee_bps != 0:
            return
        if self._free_fee_ends_at is None:
            return  # manual override=0, never auto-expire
        if datetime.now(timezone.utc) < self._free_fee_ends_at:
            return
        # Expired — clear in DB then update in-memory state
        if not self._db_factory:
            return
        try:
            from app.db.models import User as _User
            async with self._db_factory() as db:
                user = await db.get(_User, self.user_id)
                if not user:
                    return
                user.fee_override_bps = None
                await db.commit()
                restored = user.effective_fee_bps
            self._user_fee_bps = restored
            self._fee_is_overridden = False
            self._free_fee_ends_at = None
            await self._log_session("INFO", f"Free-fee period expired — fees restored to {restored} bps")
        except Exception as e:
            await self._log_session("WARN", f"free_fee_expiry clear error: {e}")

    async def _check_soft_stop_complete(self):
        """When soft-stopping, finalize once all slots are clean (no open lots, no pending buys)."""
        if self.health_state != SessionHealthState.STOPPING:
            return
        all_clean = all(slot.is_clean for slot in self._slot_runtimes.values())
        if not all_clean:
            return
        # Force-sweep any residual dust from fractional token rounding before closing
        for slot in self._slot_runtimes.values():
            await slot.sweep_all_dust()
        await self._log_session("INFO", "All positions exited — session stopped")
        try:
            close_result = await run_nuclear_close(
                self._client,
                cancel_orders=True,
                sell_all_positions=True,
            )
            await self._log_session(
                "INFO",
                f"Soft stop nuclear close: cancelled={len(close_result['cancelled_orders'])} sold={len(close_result['sold_positions'])} redeemed={len(close_result.get('redeemed_positions', []))} errors={len(close_result['errors'])}",
            )
        except Exception as e:
            await self._log_session("ERROR", f"Soft stop nuclear close failed: {e}")
        self._running      = False
        self.status        = "stopped"
        self.health_state  = SessionHealthState.STOPPED
        self.stop_reason   = self.stop_reason or "soft stop complete"
        if self._monitor_task:
            self._monitor_task.cancel()
        if self._snapshot_task:
            self._snapshot_task.cancel()
        # Remove from registry so a new session can be launched cleanly
        try:
            from app.runtime.session_registry import session_registry
            await session_registry.remove(self.session_id)
        except Exception:
            pass
        await self._write_final_snapshot()
        if self._db_factory:
            try:
                from app.db.models import Session as DBSession, AuditLog
                async with self._db_factory() as db:
                    await db.execute(
                        sa_update(DBSession)
                        .where(DBSession.id == self.session_id)
                        .values(
                            status="stopped",
                            health_state="stopped",
                            stopped_at=datetime.now(timezone.utc),
                            stop_reason=self.stop_reason,
                        )
                    )
                    db.add(AuditLog(
                        actor_id="system", actor_role="system",
                        action="session.soft_stop_complete",
                        entity_type="session", entity_id=self.session_id,
                    ))
                    await db.commit()
            except Exception as e:
                await self._log_session("ERROR", f"Failed to persist soft stop: {e}")

    async def _check_arbitrum_fee_buffer(self) -> None:
        """Soft-stop if Arbitrum USDC or ETH drops below minimum (Reserve 1 exhausted)."""
        if not self._fee_wallet_address or not self._running:
            return
        now = time.monotonic()
        if now - self._last_arb_balance_check < settings.ARB_BALANCE_CHECK_INTERVAL:
            return
        self._last_arb_balance_check = now

        from app.services.balance_poller import balance_poller
        usdc, eth = balance_poller.get_arb(self.user_id)

        if usdc is not None and usdc < settings.FEE_WALLET_USDC_STOP:
            await self._log_session(
                "WARN",
                f"Arbitrum USDC critically low (${usdc:.2f} < ${settings.FEE_WALLET_USDC_STOP:.2f}) — soft stop",
            )
            await self.soft_stop()
            return

        if eth is not None and eth < settings.FEE_WALLET_ETH_MIN:
            await self._log_session(
                "WARN",
                f"Arbitrum ETH low ({eth:.6f} < {settings.FEE_WALLET_ETH_MIN:.6f}) — soft stop",
            )
            await self.soft_stop()

    async def _check_session_sl(self):
        """
        Session SL = gross_capital * session_sl_pct / 100
        Loss metric = paid_fees + unrealized losses (conservative)
        If triggered → hard stop.
        """
        total_loss = self.fee_reserve_used + abs(min(0.0, self.total_realized_pnl))
        if self.session_sl_pct > 0:
            sl_threshold = self.gross_capital * self.session_sl_pct / 100
            if total_loss >= sl_threshold:
                reason = (
                    f"Session SL triggered: loss ${total_loss:.2f} >= "
                    f"threshold ${sl_threshold:.2f} ({self.session_sl_pct}%)"
                )
                await self._log_session("WARN", reason)
                await self.hard_stop(reason)

    async def _check_stop_mode(self):
        if not self._running:
            return
        if self.health_state == SessionHealthState.STOPPING:
            return  # already triggered — don't re-log
        if self.stop_mode == StopMode.DAYS and self.stop_days:
            elapsed_days = (time.monotonic() - self._start_time) / 86400
            if elapsed_days >= self.stop_days:
                await self._log_session("INFO", f"Stop mode days reached ({self.stop_days}d)")
                await self.soft_stop()
        elif self.stop_mode == StopMode.VOLUME and self.target_volume:
            if self.total_volume >= self.target_volume:
                await self._log_session("INFO", f"Stop mode volume reached (${self.total_volume:.0f})")
                await self.soft_stop()

    async def _snapshot_loop(self):
        while self._running:
            await asyncio.sleep(self.SNAPSHOT_INTERVAL)
            if self._on_snapshot:
                try:
                    await self._on_snapshot(self.summary.model_dump())
                except Exception:
                    pass
            else:
                try:
                    await self._write_final_snapshot()
                except Exception:
                    pass
            # Persist per-slot stats so recovery can restore them after restart
            await self._persist_slot_stats()

    async def _persist_slot_stats(self):
        if not self._db_factory:
            return
        try:
            from app.db.models import SessionSlot as DBSessionSlot
            async with self._db_factory() as db:
                for slot_id, slot in self._slot_runtimes.items():
                    await db.execute(
                        sa_update(DBSessionSlot)
                        .where(DBSessionSlot.id == slot_id)
                        .values(
                            volume=round(slot._volume, 6),
                            realized_pnl=round(slot._realized_pnl, 6),
                            unrealized_pnl=round(slot._unrealized_pnl, 6),
                            paid_fees=round(slot._paid_fees, 6),
                            deployed_capital=round(slot.deployed_capital, 6),
                        )
                    )
                await db.commit()
        except Exception as e:
            await self._log_session("WARN", f"_persist_slot_stats failed: {e}")

    # ── Slot callbacks ────────────────────────────────────────────────────────

    async def _on_lot_opened(self, session_slot_id: str, lot: RuntimeLot):
        if self._strategy and self._privy_wallet_id and self._fee_wallet_address:
            await fee_service.process_lot_opened(
                self,
                lot.size,
                self._strategy,
                slot_id=session_slot_id,
                user_fee_bps=self._user_fee_bps,
                privy_wallet_id=self._privy_wallet_id,
                wallet_address=self._fee_wallet_address,
                referrer_user_id=self._referrer_user_id,
                referrer_wallet_address=self._referrer_wallet_address,
                db_factory=self._db_factory,
            )
        # Increment lifetime trading_volume (buy-side notional)
        await self._increment_user_volume(lot.size, pnl=None)
        await self._increment_slot_volume(session_slot_id, lot.size, pnl=None)
        if self._on_lot_event:
            await self._on_lot_event("opened", lot, None)

    async def _on_lot_closed(self, session_slot_id: str, lot: RuntimeLot, pnl: float):
        if self._strategy and self._privy_wallet_id and self._fee_wallet_address:
            await fee_service.process_lot_closed(
                self,
                lot.size,
                self._strategy,
                slot_id=session_slot_id,
                user_fee_bps=self._user_fee_bps,
                privy_wallet_id=self._privy_wallet_id,
                wallet_address=self._fee_wallet_address,
                referrer_user_id=self._referrer_user_id,
                referrer_wallet_address=self._referrer_wallet_address,
                db_factory=self._db_factory,
            )
        # Increment lifetime trading_volume (sell-side notional) + bot_pnl
        await self._increment_user_volume(lot.size, pnl=pnl)
        await self._increment_slot_volume(session_slot_id, lot.size, pnl=pnl)
        if self._on_lot_event:
            await self._on_lot_event("closed", lot, pnl)

    async def _increment_slot_volume(self, session_slot_id: str, size: float, pnl: Optional[float]):
        """Atomically increment SessionSlot volume/pnl in DB on every fill — no 5-min gap."""
        if not self._db_factory:
            return
        try:
            from app.db.models import SessionSlot as DBSessionSlot
            values: dict = {"volume": DBSessionSlot.volume + size}
            if pnl is not None:
                values["realized_pnl"] = DBSessionSlot.realized_pnl + pnl
            async with self._db_factory() as db:
                await db.execute(
                    sa_update(DBSessionSlot)
                    .where(DBSessionSlot.id == session_slot_id)
                    .values(**values)
                )
                await db.commit()
        except Exception:
            pass  # non-critical — snapshot loop covers it

    async def _increment_user_volume(self, size: float, pnl: Optional[float]):
        """
        Atomically increment User.trading_volume (+size) and User.bot_pnl (+pnl if provided).
        On volume threshold crossing, update effective_fee_bps and refresh in-memory cache.
        """
        if not self._db_factory:
            return
        try:
            from app.db.models import User
            self._user_trading_volume = round(self._user_trading_volume + size, 6)
            new_xp_fee = _compute_xp_fee_bps(self._user_trading_volume)

            values: dict = {"trading_volume": User.trading_volume + size}
            if pnl is not None:
                values["bot_pnl"] = User.bot_pnl + pnl

            # Always persist the XP fee to effective_fee_bps in DB (source of truth for XP)
            # Only update in-memory _user_fee_bps if no admin override is active
            xp_level_changed = new_xp_fee != _compute_xp_fee_bps(self._user_trading_volume - size)
            if xp_level_changed:
                values["effective_fee_bps"] = new_xp_fee
                if not self._fee_is_overridden:
                    self._user_fee_bps = new_xp_fee
                await self._log_session(
                    "INFO",
                    f"XP level up: effective_fee_bps → {new_xp_fee} "
                    f"(volume ${self._user_trading_volume:,.0f})"
                    + (" [override active, fee unchanged]" if self._fee_is_overridden else "")
                )

            async with self._db_factory() as db:
                await db.execute(
                    sa_update(User)
                    .where(User.id == self.user_id)
                    .values(**values)
                )
                await db.commit()
        except Exception as e:
            await self._log_session("WARN", f"_increment_user_volume failed: {e}")

    async def _on_slot_state(self, session_slot_id: str, state: SlotState, reason: str):
        # Persist slot_state to DB so admin view stays in sync
        if self._db_factory:
            try:
                from app.db.models import SessionSlot as DBSessionSlot
                state_val = state.value if hasattr(state, "value") else str(state)
                async with self._db_factory() as db:
                    await db.execute(
                        sa_update(DBSessionSlot)
                        .where(DBSessionSlot.id == session_slot_id)
                        .values(slot_state=state_val)
                    )
                    await db.commit()
            except Exception:
                pass
        await self._broadcast()

    async def _on_slot_log(self, session_slot_id: str, level: str, msg: str):
        # Replace [slot:XXXXXXXX] prefix with short question label if available
        slot = self._slot_runtimes.get(session_slot_id)
        if slot and slot._market_config and slot._market_config.question:
            q = slot._market_config.question[:30]
            display_msg = msg.replace(f"[slot:{session_slot_id[:8]}]", f"[{q}]", 1)
        else:
            display_msg = msg
        await self._log_session(level, display_msg, session_slot_id=session_slot_id)

    # ── Aggregates ────────────────────────────────────────────────────────────

    @property
    def total_deployed(self) -> float:
        return sum(s.deployed_capital for s in self._slot_runtimes.values())

    @property
    def total_realized_pnl(self) -> float:
        return sum(s._realized_pnl for s in self._slot_runtimes.values())

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(s._unrealized_pnl for s in self._slot_runtimes.values())

    @property
    def total_volume(self) -> float:
        return sum(s._volume for s in self._slot_runtimes.values())

    @property
    def total_paid_fees(self) -> float:
        return sum(s._paid_fees for s in self._slot_runtimes.values())

    @property
    def fee_reserve_remaining(self) -> float:
        return max(0.0, self.fee_reserve - self.fee_reserve_used)

    @property
    def uptime_s(self) -> float:
        return time.monotonic() - self._start_time if self._running else 0.0

    @property
    def summary(self) -> SessionRuntimeSummary:
        return SessionRuntimeSummary(
            session_id=self.session_id,
            user_id=self.user_id,
            health_state=self.health_state,
            status=self.status,
            gross_capital=self.gross_capital,
            deployable_capital=self.deployable_capital,
            fee_reserve_used=self.fee_reserve_used,
            fee_reserve_remaining=self.fee_reserve_remaining,
            total_deployed=self.total_deployed,
            total_volume=self.total_volume,
            realized_pnl=self.total_realized_pnl,
            unrealized_pnl=self.total_unrealized_pnl,
            total_pnl=round(self.total_realized_pnl + self.total_unrealized_pnl, 6),
            paid_fees=self.total_paid_fees,
            slot_count=len(self._slot_runtimes),
            active_slot_count=sum(
                1 for s in self._slot_runtimes.values()
                if s.state == SlotState.ACTIVE
            ),
            trade_count=sum(s._trade_count for s in self._slot_runtimes.values()),
            order_count=sum(s._order_count for s in self._slot_runtimes.values()),
            uptime_s=self.uptime_s,
        )

    def slot_summary(self, session_slot_id: str) -> Optional[dict]:
        slot = self._slot_runtimes.get(session_slot_id)
        return slot.stats if slot else None

    def all_slot_stats(self) -> list[dict]:
        return [s.stats for s in self._slot_runtimes.values()]

    def get_slot_runtime(self, session_slot_id: str) -> Optional[SessionSlotRuntime]:
        return self._slot_runtimes.get(session_slot_id)

    # ── Log / broadcast helpers ───────────────────────────────────────────────

    async def _log_session(self, level: str, msg: str, session_slot_id: str = ""):
        # Dedup: collapse repeated identical messages into "×N" suffix
        dedup_key = f"{level}:{session_slot_id}"
        prev_msg, prev_count = self._log_last.get(dedup_key, ("", 0))
        if msg == prev_msg:
            # Same message — update count on last entry instead of appending
            self._log_last[dedup_key] = (msg, prev_count + 1)
            if self._log_buffer:
                self._log_buffer[-1]["repeat"] = prev_count + 1
            return
        # New message — flush any pending repeat count label, then append
        self._log_last[dedup_key] = (msg, 1)

        entry = {
            "level": level,
            "message": msg,
            "session_slot_id": session_slot_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "repeat": 1,
        }
        self._log_buffer.append(entry)
        if len(self._log_buffer) > self._log_max:
            self._log_buffer = self._log_buffer[-self._log_max:]
        if self._on_log:
            try:
                await self._on_log(level, msg, session_slot_id)
            except Exception:
                pass

    async def _broadcast(self):
        if self._on_broadcast:
            try:
                await self._on_broadcast()
            except Exception:
                pass

    def recent_logs(self, n: int = 200) -> list[dict]:
        return self._log_buffer[-n:]

    def charge_fee(self, amount: float, slot_id: str | None = None):
        """Record a platform fee charge from the fee reserve."""
        self.fee_reserve_used = round(self.fee_reserve_used + amount, 6)
        slot = self._slot_runtimes.get(slot_id) if slot_id else None
        if slot:
            slot.update_paid_fees(amount)
