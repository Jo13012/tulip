"""
session_manager.py — Session lifecycle manager.

Handles:
  - session launch (viability check → capital split → slot init → engine start)
  - session stop (soft/hard)
  - slot replacement lifecycle
  - recovery after restart
  - global pause enforcement
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.db.models import (
    Session as DBSession, SessionSlot as DBSessionSlot,
    SessionSlotMarketHistory, SessionLot as DBLot,
    Strategy, StrategyVersion, StrategySlot, StrategySlotVersion,
    Wallet, User, AuditLog,
)
from app.db.session import async_session_factory
from app.runtime.runtime_models import SlotMarketConfig, StopMode, Side
from app.runtime.session_engine import SessionEngine
from app.runtime.session_registry import session_registry
from app.integrations.polymarket.execution_client import ExecutionClient
from app.integrations.polymarket.market_resolver import market_resolver
from app.core.config import settings

MIN_LOT_USDC = 5   # Practical minimum USDC per lot


class SessionLaunchError(Exception):
    pass


class ViabilityResult:
    def __init__(self, viable: bool, min_capital: float, recommended_capital: float, reason: str = ""):
        self.viable              = viable
        self.min_capital         = min_capital
        self.recommended_capital = recommended_capital
        self.reason              = reason


def compute_viability(
    slots: list[dict],   # [{allocation_pct, max_spread_ticks}, ...]
    reserve_pct: float,
    gross_capital: float,
) -> ViabilityResult:
    """
    Check if gross_capital is sufficient to run all enabled slots.

    The bot deploys only `deployable = gross * (1 - reserve/100)` — the reserve
    is already withheld before any slot ever sees capital. So viability works
    purely in deployable space:

      min_deployable_for_slot = (levels × MIN_LOT_USDC) / alloc_pct
        → because slot gets alloc_pct of deployable, and needs levels × MIN_LOT_USDC

    The most capital-hungry slot sets the floor.
    min_capital / recommended_capital are expressed in gross for the UI.
    """
    deployable = gross_capital * (1 - reserve_pct / 100) 
    enabled = [s for s in slots if s.get("enabled", True) and s.get("allocation_pct", 0) > 0]
    if not enabled:
        return ViabilityResult(True, 0.0, 0.0, "No enabled slots")

    # min_deployable = max( (levels_i × MIN_LOT) / alloc_pct_i )
    # The most constrained slot sets the floor for total deployable capital.
    min_dep = 0.0
    for slot in enabled:
        levels    = slot.get("max_spread_ticks", 0) + 1
        alloc_pct = slot.get("allocation_pct", 0) / 100
        if alloc_pct <= 0:
            continue
        min_dep = max(min_dep, (levels * MIN_LOT_USDC) / alloc_pct)

    # Convert back to gross for UI display
    factor      = (1 - reserve_pct / 100)
    min_gross   = min_dep / factor if factor > 0 else min_dep
    rec_gross   = min_gross * 3.0

    viable = deployable >= min_dep
    reason = (
        "" if viable else
        f"Minimum required is ${min_gross:.2f} gross (${min_dep:.2f} deployed). "
        f"Each slot needs at least ${MIN_LOT_USDC} per lot level."
    )
    return ViabilityResult(viable, round(min_gross, 2), round(rec_gross, 2), reason)


async def _build_slot_market_config(
    ssv: StrategySlotVersion,
    lot_size: float,
) -> Optional[SlotMarketConfig]:
    """Build SlotMarketConfig from a slot version. Returns None if no market assigned."""
    if not ssv.polymarket_url:
        return None

    # If condition_id already resolved in the slot version, use it
    if ssv.condition_id and ssv.token_id_yes and ssv.token_id_no:
        active_token = ssv.token_id_yes if ssv.side == "YES" else ssv.token_id_no
        return SlotMarketConfig(
            polymarket_url=ssv.polymarket_url,
            question=ssv.question,
            condition_id=ssv.condition_id,
            token_id_yes=ssv.token_id_yes,
            token_id_no=ssv.token_id_no,
            active_token_id=active_token,
            tick_size="0.001",
            neg_risk=False,
            side=Side(ssv.side or "YES"),
            price_min=ssv.price_min or 0.9,
            price_max=ssv.price_max or 0.99,
            max_spread_ticks=ssv.max_spread_ticks if ssv.max_spread_ticks is not None else 1,
            sl_ticks=ssv.sl_ticks or 0,
            spread_from_ask=ssv.spread_from_ask or False,
            lot_size=lot_size,
        )

    # Resolve from Gamma API
    try:
        info = await market_resolver.resolve_and_validate(ssv.polymarket_url)
        active_token = info["token_id_yes"] if (ssv.side or "YES") == "YES" else info["token_id_no"]
        return SlotMarketConfig(
            polymarket_url=ssv.polymarket_url,
            question=ssv.question or info["question"],
            condition_id=info["condition_id"],
            token_id_yes=info["token_id_yes"],
            token_id_no=info["token_id_no"],
            active_token_id=active_token,
            tick_size=info["tick_size"],
            neg_risk=info["neg_risk"],
            side=Side(ssv.side or "YES"),
            price_min=ssv.price_min or 0.9,
            price_max=ssv.price_max or 0.99,
            max_spread_ticks=ssv.max_spread_ticks if ssv.max_spread_ticks is not None else 1,
            sl_ticks=ssv.sl_ticks or 0,
            spread_from_ask=ssv.spread_from_ask or False,
            lot_size=lot_size,
        )
    except Exception:
        return None


class SessionManager:
    """
    Manages session lifecycle.
    Not a singleton — but called from API routes (one instance per request is fine).
    Persists state to DB; runtime state to session_registry.
    """

    async def check_viability(
        self,
        strategy_id: str,
        gross_capital: float,
    ) -> ViabilityResult:
        async with async_session_factory() as db:
            strat = await db.get(Strategy, strategy_id)
            if not strat:
                return ViabilityResult(False, 0.0, 0.0, "Strategy not found")

            slots_q = await db.execute(
                select(StrategySlot).where(
                    StrategySlot.strategy_id == strategy_id,
                    StrategySlot.archived_at.is_(None),
                )
            )
            slots = slots_q.scalars().all()

            # Find latest published version
            ver_q = await db.execute(
                select(StrategyVersion)
                .where(StrategyVersion.strategy_id == strategy_id)
                .order_by(StrategyVersion.published_at.desc())
                .limit(1)
            )
            version = ver_q.scalars().first()
            if not version:
                return ViabilityResult(False, 0.0, 0.0, "No published strategy version")

            # Get slot versions
            ssv_q = await db.execute(
                select(StrategySlotVersion).where(
                    StrategySlotVersion.strategy_version_id == version.id
                )
            )
            slot_versions = {sv.strategy_slot_id: sv for sv in ssv_q.scalars().all()}

            slot_configs = []
            for slot in slots:
                sv = slot_versions.get(slot.id)
                slot_configs.append({
                    "enabled": slot.enabled,
                    "allocation_pct": slot.allocation_pct,
                    "max_spread_ticks": sv.max_spread_ticks if sv else 1,
                })

            return compute_viability(slot_configs, strat.reserve_pct, gross_capital)

    async def launch_session(
        self,
        user_id: str,
        wallet_id: str,
        strategy_id: str,
        gross_capital: float,
        stop_mode: str,
        stop_days: Optional[int],
        target_volume: Optional[float],
        session_sl_pct: float,
    ) -> DBSession:
        async with async_session_factory() as db:
            # Guard: max 1 active session per user
            existing = await db.execute(
                select(DBSession).where(
                    DBSession.user_id == user_id,
                    DBSession.status.in_(["pending", "starting", "running", "recovering"]),
                )
            )
            if existing.scalars().first():
                raise SessionLaunchError("User already has an active session")

            strat = await db.get(Strategy, strategy_id)
            if not strat:
                raise SessionLaunchError("Strategy not found")
            if strat.status != "published":
                raise SessionLaunchError("Strategy is not published")
            if strat.global_pause:
                raise SessionLaunchError("Platform is globally paused")

            wallet = await db.get(Wallet, wallet_id)
            if not wallet:
                wallet_q = await db.execute(
                    select(Wallet).where(
                        Wallet.user_id == user_id,
                        Wallet.wallet_address == wallet_id,
                    )
                )
                wallet = wallet_q.scalars().first()

            if not wallet or wallet.user_id != user_id:
                raise SessionLaunchError("Wallet not found or not owned by user")

            # Latest published version
            ver_q = await db.execute(
                select(StrategyVersion)
                .where(StrategyVersion.strategy_id == strategy_id)
                .order_by(StrategyVersion.published_at.desc())
                .limit(1)
            )
            version = ver_q.scalars().first()
            if not version:
                raise SessionLaunchError("No published strategy version")

            # Viability check
            viability = await self.check_viability(strategy_id, gross_capital)
            if not viability.viable:
                raise SessionLaunchError(f"Capital insufficient: {viability.reason}")

            # Trading balance guard — only Polymarket balance matters now
            from app.services.balance_poller import balance_poller
            trading_bal = balance_poller.get_trading(user_id)
            positions_val = balance_poller.get(user_id).positions_value or 0.0
            trading_cash = (trading_bal or 0.0)  # cash available (excludes positions)
            if trading_cash <= 0:
                raise SessionLaunchError(
                    "No Polymarket trading balance. "
                    "Deposit USDC to your Polymarket account via Profile → Deposit."
                )
            if gross_capital > trading_cash + positions_val:
                raise SessionLaunchError(
                    f"Capital ${gross_capital:.2f} exceeds available Polymarket balance "
                    f"${trading_cash + positions_val:.2f}. "
                    "Deposit more USDC or reduce capital."
                )

            # Capital split
            reserve_pct = strat.reserve_pct
            fee_reserve = round(gross_capital * reserve_pct / 100, 2)
            deployable  = round(gross_capital - fee_reserve, 2)

            # Create session
            session = DBSession(
                user_id=user_id,
                wallet_id=wallet_id,
                strategy_id=strategy_id,
                start_strategy_version_id=version.id,
                session_gross_capital=gross_capital,
                session_deployable_capital=deployable,
                session_fee_reserve=fee_reserve,
                stop_mode=stop_mode,
                stop_days=stop_days,
                target_volume=target_volume,
                session_sl_pct=session_sl_pct,
                status="starting",
                health_state="starting",
            )
            db.add(session)
            await db.flush()

            # Create session slots from strategy slots
            slots_q = await db.execute(
                select(StrategySlot).where(
                    StrategySlot.strategy_id == strategy_id,
                    StrategySlot.archived_at.is_(None),
                ).order_by(StrategySlot.order_index)
            )
            slots = slots_q.scalars().all()

            ssv_q = await db.execute(
                select(StrategySlotVersion).where(
                    StrategySlotVersion.strategy_version_id == version.id
                )
            )
            slot_versions = {sv.strategy_slot_id: sv for sv in ssv_q.scalars().all()}

            session_slots = []
            for slot in slots:
                sv = slot_versions.get(slot.id)
                allocated = round(deployable * slot.allocation_pct / 100, 2)
                ss = DBSessionSlot(
                    session_id=session.id,
                    strategy_slot_id=slot.id,
                    current_strategy_slot_version_id=sv.id if sv else None,
                    slot_state="idle",  # runtime sets to active on start
                    allocation_pct=slot.allocation_pct,
                    allocated_capital=allocated,
                )
                db.add(ss)
                await db.flush()

                # Market history entry
                if sv and sv.polymarket_url:
                    mh = SessionSlotMarketHistory(
                        session_slot_id=ss.id,
                        applied_strategy_version_id=version.id,
                        applied_strategy_slot_version_id=sv.id,
                        polymarket_url=sv.polymarket_url,
                        question=sv.question,
                        condition_id=sv.condition_id,
                        token_id_yes=sv.token_id_yes,
                        token_id_no=sv.token_id_no,
                        active_token_id=sv.token_id_yes if sv.side == "YES" else sv.token_id_no,
                        side=sv.side,
                    )
                    db.add(mh)

                session_slots.append((ss, sv, allocated))

            # Audit log
            db.add(AuditLog(
                actor_id=user_id,
                actor_role="user",
                action="session.launch",
                entity_type="session",
                entity_id=session.id,
                detail=f"capital=${gross_capital} mode={stop_mode}",
            ))
            await db.commit()
            await db.refresh(session)

            # Start runtime engine (non-blocking)
            asyncio.create_task(
                self._start_runtime(
                    session=session,
                    wallet=wallet,
                    session_slots=session_slots,
                    slot_versions=slot_versions,
                    strategy=strat,
                )
            )
            return session

    async def _start_runtime(
        self,
        session: DBSession,
        wallet: Wallet,
        session_slots: list,
        slot_versions: dict,
        strategy: Strategy,
    ):
        try:
            if not wallet.privy_wallet_id:
                raise SessionLaunchError("No Privy wallet linked to this account. Please log out and log in again.")

            # Load per-user fee config
            async with async_session_factory() as db:
                user = await db.get(User, session.user_id)
                if user:
                    # Override takes priority; NULL means use XP-computed fee
                    fee_is_overridden = user.fee_override_bps is not None
                    user_fee_bps = (
                        user.fee_override_bps
                        if fee_is_overridden
                        else user.effective_fee_bps
                    )
                    user_trading_volume = user.trading_volume
                    referrer_user_id = user.referrer_user_id or ""
                    _ffa = user.free_fee_ends_at
                    free_fee_ends_at = _ffa if (fee_is_overridden and _ffa and datetime.now(timezone.utc) < _ffa) else None
                else:
                    fee_is_overridden = False
                    user_fee_bps = 120
                    user_trading_volume = 0.0
                    referrer_user_id = ""
                    free_fee_ends_at = None

                referrer_wallet_address = ""
                if referrer_user_id:
                    ref_wallet_q = await db.execute(
                        select(Wallet.wallet_address).where(
                            Wallet.user_id == referrer_user_id,
                            Wallet.is_primary == True,
                        )
                    )
                    referrer_wallet_address = ref_wallet_q.scalar() or ""

            client = ExecutionClient(
                privy_wallet_id=wallet.privy_wallet_id,
                wallet_address=wallet.wallet_address,
                proxy_address=wallet.proxy_address or "",
                display_name=user.display_name if user else "",
            )
            await client.initialize()

            # Create engine
            engine = SessionEngine(
                session_id=session.id,
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
                strategy=strategy,
                privy_wallet_id=wallet.privy_wallet_id or "",
                fee_wallet_address=wallet.wallet_address,
                referrer_user_id=referrer_user_id,
                referrer_wallet_address=referrer_wallet_address,
                user_fee_bps=user_fee_bps,
                fee_is_overridden=fee_is_overridden,
                user_trading_volume=user_trading_volume,
                free_fee_ends_at=free_fee_ends_at,
                db_factory=async_session_factory,
            )

            await session_registry.add(session.id, engine)
            await engine.start()

            # Add slots
            for ss, sv, allocated in session_slots:
                if not sv:
                    await engine.add_slot(ss.id, ss.strategy_slot_id, None, 0.0)
                    continue
                levels = sv.max_spread_ticks + 1
                lot_size = round(allocated / levels, 4) if levels > 0 else 0.0
                config = await _build_slot_market_config(sv, lot_size)
                await engine.add_slot(ss.id, ss.strategy_slot_id, config, allocated)

            # Update DB status
            async with async_session_factory() as db:
                await db.execute(
                    update(DBSession)
                    .where(DBSession.id == session.id)
                    .values(
                        status="running",
                        health_state="running",
                        started_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()

        except Exception as e:
            async with async_session_factory() as db:
                await db.execute(
                    update(DBSession)
                    .where(DBSession.id == session.id)
                    .values(status="error", health_state="error", stop_reason=str(e))
                )
                await db.commit()

    async def stop_session(
        self,
        session_id: str,
        mode: str = "soft",
        actor_id: str = "system",
    ):
        engine = await session_registry.get(session_id)
        if engine:
            if mode == "hard":
                # engine.hard_stop writes the final snapshot + DB status update internally
                await engine.hard_stop(reason=f"manual hard stop by {actor_id}")
                await session_registry.remove(session_id)
            else:
                await engine.soft_stop()

        if mode == "soft":
            # For soft stop, just mark DB as stopping; final update happens in engine when clean
            async with async_session_factory() as db:
                await db.execute(
                    update(DBSession)
                    .where(DBSession.id == session_id)
                    .values(
                        status="stopping",
                        health_state="stopping",
                        stop_reason=f"manual soft stop by {actor_id}",
                    )
                )
                db.add(AuditLog(
                    actor_id=actor_id,
                    actor_role="user" if actor_id != "system" else "system",
                    action="session.soft_stop",
                    entity_type="session",
                    entity_id=session_id,
                ))
                await db.commit()
        else:
            # Hard stop — engine wrote snapshot+status if it was alive; add audit log.
            # If no engine was found, we must update DB status ourselves.
            async with async_session_factory() as db:
                if not engine:
                    await db.execute(
                        update(DBSession)
                        .where(DBSession.id == session_id)
                        .values(
                            status="stopped",
                            health_state="stopped",
                            stopped_at=datetime.now(timezone.utc),
                            stop_reason=f"manual hard stop by {actor_id}",
                        )
                    )
                db.add(AuditLog(
                    actor_id=actor_id,
                    actor_role="user" if actor_id != "system" else "system",
                    action="session.hard_stop",
                    entity_type="session",
                    entity_id=session_id,
                ))
                await db.commit()


# Shared singleton
session_manager = SessionManager()
