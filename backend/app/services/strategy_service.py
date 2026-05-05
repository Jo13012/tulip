"""
strategy_service.py — Strategy CRUD + versioning + slot management.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Strategy, StrategyVersion, StrategySlot, StrategySlotVersion, AuditLog
)


class StrategyService:

    # ── Strategy ──────────────────────────────────────────────────────────────

    async def create_strategy(
        self,
        db: AsyncSession,
        name: str,
        reserve_pct: float,
        fee_side: str,
        actor_id: str,
    ) -> Strategy:
        strat = Strategy(
            name=name,
            status="draft",
            reserve_pct=reserve_pct,
            fee_side=fee_side,
        )
        db.add(strat)
        await db.flush()
        db.add(AuditLog(
            actor_id=actor_id, actor_role="admin",
            action="strategy.create", entity_type="strategy", entity_id=strat.id,
        ))
        await db.commit()
        await db.refresh(strat)
        return strat

    async def update_strategy(
        self,
        db: AsyncSession,
        strategy_id: str,
        actor_id: str,
        **fields,
    ) -> Optional[Strategy]:
        strat = await db.get(Strategy, strategy_id)
        if not strat:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(strat, k):
                setattr(strat, k, v)
        db.add(AuditLog(
            actor_id=actor_id, actor_role="admin",
            action="strategy.update", entity_type="strategy", entity_id=strategy_id,
            detail=json.dumps({k: str(v) for k, v in fields.items() if v is not None}),
        ))
        await db.commit()
        await db.refresh(strat)
        return strat

    async def publish_strategy(
        self,
        db: AsyncSession,
        strategy_id: str,
        kind: str,  # "minor" | "major"
        actor_id: str,
    ) -> StrategyVersion:
        strat = await db.get(Strategy, strategy_id)
        if not strat:
            raise ValueError("Strategy not found")

        new_major = strat.current_major
        new_minor = strat.current_minor

        if kind == "major":
            new_major = strat.current_major + 1
            new_minor = 0
        else:
            new_minor = strat.current_minor + 1

        version_label = f"{new_major}.{new_minor}"

        version = StrategyVersion(
            strategy_id=strategy_id,
            major=new_major,
            minor=new_minor,
            version_label=version_label,
            published_at=datetime.now(timezone.utc),
        )
        db.add(version)
        await db.flush()

        # Snapshot current slot configs into this version
        slots_q = await db.execute(
            select(StrategySlot).where(
                StrategySlot.strategy_id == strategy_id,
                StrategySlot.archived_at.is_(None),
            ).order_by(StrategySlot.order_index)
        )
        for slot in slots_q.scalars().all():
            # Find latest slot version to snapshot
            latest_sv_q = await db.execute(
                select(StrategySlotVersion)
                .where(StrategySlotVersion.strategy_slot_id == slot.id)
                .order_by(StrategySlotVersion.created_at.desc())
                .limit(1)
            )
            latest_sv = latest_sv_q.scalars().first()

            ssv = StrategySlotVersion(
                strategy_version_id=version.id,
                strategy_slot_id=slot.id,
                polymarket_url=latest_sv.polymarket_url if latest_sv else None,
                question=latest_sv.question if latest_sv else None,
                condition_id=latest_sv.condition_id if latest_sv else None,
                token_id_yes=latest_sv.token_id_yes if latest_sv else None,
                token_id_no=latest_sv.token_id_no if latest_sv else None,
                side=latest_sv.side if latest_sv else "YES",
                price_min=latest_sv.price_min if latest_sv else None,
                price_max=latest_sv.price_max if latest_sv else None,
                max_spread_ticks=latest_sv.max_spread_ticks if latest_sv else 1,
                sl_ticks=latest_sv.sl_ticks if latest_sv else 0,
                spread_from_ask=latest_sv.spread_from_ask if latest_sv else False,
                allocation_pct=slot.allocation_pct,  # snapshot live value at publish time
            )
            db.add(ssv)

        # Update strategy version counters and status
        strat.current_major = new_major
        strat.current_minor = new_minor
        strat.status = "published"
        db.add(strat)

        db.add(AuditLog(
            actor_id=actor_id, actor_role="admin",
            action="strategy.publish", entity_type="strategy", entity_id=strategy_id,
            detail=f"version={version_label} kind={kind}",
        ))
        await db.commit()
        await db.refresh(version)
        return version

    # ── Slots ─────────────────────────────────────────────────────────────────

    async def create_slot(
        self,
        db: AsyncSession,
        strategy_id: str,
        label: str,
        allocation_pct: float,
        order_index: int,
        actor_id: str,
    ) -> StrategySlot:
        slot = StrategySlot(
            strategy_id=strategy_id,
            label=label,
            allocation_pct=allocation_pct,
            order_index=order_index,
            enabled=True,
        )
        db.add(slot)
        await db.flush()
        db.add(AuditLog(
            actor_id=actor_id, actor_role="admin",
            action="slot.create", entity_type="strategy_slot", entity_id=slot.id,
        ))
        await db.commit()
        await db.refresh(slot)
        return slot

    async def set_slot_market(
        self,
        db: AsyncSession,
        slot_id: str,
        strategy_version_id: str,   # version context (or "draft")
        polymarket_url: str,
        question: Optional[str],
        condition_id: Optional[str],
        token_id_yes: Optional[str],
        token_id_no: Optional[str],
        side: str,
        price_min: float,
        price_max: float,
        number_of_lots: int,   # UI concept: converts to max_spread_ticks = n-1
        sl_ticks: int,
        spread_from_ask: bool,
        actor_id: str,
    ) -> StrategySlotVersion:
        max_spread_ticks = max(0, number_of_lots - 1)

        # "draft" is a sentinel — not a real FK; use None for drafts
        real_version_id = strategy_version_id if strategy_version_id != "draft" else None

        ssv = StrategySlotVersion(
            strategy_version_id=real_version_id,
            strategy_slot_id=slot_id,
            polymarket_url=polymarket_url,
            question=question,
            condition_id=condition_id,
            token_id_yes=token_id_yes,
            token_id_no=token_id_no,
            side=side,
            price_min=price_min,
            price_max=price_max,
            max_spread_ticks=max_spread_ticks,
            sl_ticks=sl_ticks,
            spread_from_ask=spread_from_ask,
        )

        db.add(ssv)
        await db.flush()
        db.add(AuditLog(
            actor_id=actor_id, actor_role="admin",
            action="slot.set_market", entity_type="strategy_slot", entity_id=slot_id,
            detail=f"url={polymarket_url}",
        ))
        await db.commit()
        await db.refresh(ssv)
        return ssv

    async def queue_slot_replacement(
        self,
        db: AsyncSession,
        session_slot_id: str,
        new_slot_version_id: str,
        actor_id: str,
    ):
        """
        Queue a slot replacement for an active session slot.
        Updates DB and triggers runtime engine replacement.
        """
        from app.db.models import SessionSlot as DBSessionSlot
        from app.runtime.session_registry import session_registry
        from app.runtime.session_manager import _build_slot_market_config

        ss = await db.get(DBSessionSlot, session_slot_id)
        if not ss:
            raise ValueError("SessionSlot not found")

        new_sv = await db.get(StrategySlotVersion, new_slot_version_id)
        if not new_sv:
            raise ValueError("StrategySlotVersion not found")

        ss.replacement_strategy_slot_version_id = new_slot_version_id
        ss.slot_state = "replacement_queued"
        db.add(ss)

        db.add(AuditLog(
            actor_id=actor_id, actor_role="admin",
            action="slot.replace_queued", entity_type="session_slot", entity_id=session_slot_id,
            detail=f"new_version={new_slot_version_id}",
        ))
        await db.commit()

        # Notify runtime engine
        from app.db.models import Session as DBSession
        session = await db.get(DBSession, ss.session_id)
        if session:
            engine = await session_registry.get(session.id)
            if engine:
                levels = new_sv.max_spread_ticks + 1
                allocated = ss.allocated_capital
                lot_size = round(allocated / levels, 4) if levels > 0 else 0.0
                new_config = await _build_slot_market_config(new_sv, lot_size)
                if new_config:
                    await engine.queue_slot_replacement(session_slot_id, new_config)


    async def propagate_publish_to_active_sessions(
        self,
        db: AsyncSession,
        strategy_id: str,
        version: StrategyVersion,
        actor_id: str,
    ) -> None:
        """
        After a new strategy version is published, find all active sessions
        using this strategy and queue slot replacements for any changed slots.
        Archived slots (no longer in new version) get queued with None config
        (slot runtime will idle/stop).
        """
        from app.db.models import Session as DBSession, SessionSlot as DBSessionSlot, SessionSlotMarketHistory
        from app.runtime.session_registry import session_registry
        from app.runtime.session_manager import _build_slot_market_config
        import uuid as _uuid_mod

        # All active sessions for this strategy
        sessions_q = await db.execute(
            select(DBSession).where(
                DBSession.strategy_id == strategy_id,
                DBSession.status.in_(["running", "stopping", "recovering"]),
            )
        )
        active_sessions = sessions_q.scalars().all()
        if not active_sessions:
            return

        # New slot versions indexed by strategy_slot_id
        new_svs_q = await db.execute(
            select(StrategySlotVersion).where(
                StrategySlotVersion.strategy_version_id == version.id
            )
        )
        new_svs: dict[str, StrategySlotVersion] = {
            sv.strategy_slot_id: sv for sv in new_svs_q.scalars().all()
        }

        # New strategy slots (for allocation_pct lookup)
        new_slots_q = await db.execute(
            select(StrategySlot).where(
                StrategySlot.strategy_id == strategy_id,
                StrategySlot.archived_at.is_(None),
            )
        )
        new_slots: dict[str, StrategySlot] = {
            s.id: s for s in new_slots_q.scalars().all()
        }

        for session in active_sessions:
            is_stopping = session.status == "stopping"
            deployable = session.session_deployable_capital

            # Existing session slots indexed by strategy_slot_id
            ss_q = await db.execute(
                select(DBSessionSlot).where(DBSessionSlot.session_id == session.id)
            )
            session_slots = ss_q.scalars().all()
            existing_by_strategy_slot: dict[str, DBSessionSlot] = {
                ss.strategy_slot_id: ss for ss in session_slots
            }

            engine = await session_registry.get(session.id)

            # ── Handle existing slots: modified or archived ─────────────────
            for ss in session_slots:
                new_sv = new_svs.get(ss.strategy_slot_id)
                if new_sv is None:
                    # Slot archived — idle after positions exit (skip if already transitioning)
                    if ss.slot_state not in ("replacement_queued", "waiting_previous_clean", "switching"):
                        ss.slot_state = "replacement_queued"
                    ss.replacement_strategy_slot_version_id = None
                    db.add(ss)
                    db.add(AuditLog(
                        actor_id=actor_id, actor_role="admin",
                        action="slot.replace_queued",
                        entity_type="session_slot", entity_id=ss.id,
                        detail="slot archived in new publish",
                    ))
                    if engine and not is_stopping:
                        await engine.queue_slot_replacement(ss.id, None)
                    continue

                # Compute new allocated_capital from published allocation_pct
                strat_slot = new_slots.get(ss.strategy_slot_id)
                new_alloc_pct = strat_slot.allocation_pct if strat_slot else ss.allocation_pct
                new_allocated = round(deployable * new_alloc_pct / 100, 2)
                alloc_changed = abs(new_allocated - ss.allocated_capital) > 0.001 or new_alloc_pct != ss.allocation_pct

                # Compare content vs current running version — only queue if truly changed
                cur_sv = await db.get(StrategySlotVersion, ss.current_strategy_slot_version_id) \
                         if ss.current_strategy_slot_version_id else None
                market_changed = not cur_sv or not (
                    cur_sv.condition_id     == new_sv.condition_id and
                    cur_sv.side             == new_sv.side and
                    cur_sv.price_min        == new_sv.price_min and
                    cur_sv.price_max        == new_sv.price_max and
                    cur_sv.max_spread_ticks == new_sv.max_spread_ticks and
                    cur_sv.sl_ticks         == new_sv.sl_ticks and
                    cur_sv.spread_from_ask  == new_sv.spread_from_ask
                )

                if not market_changed and not alloc_changed:
                    continue  # truly unchanged — skip

                # Update allocation in DB regardless of whether market changed
                if alloc_changed:
                    ss.allocation_pct = new_alloc_pct
                    ss.allocated_capital = new_allocated

                if market_changed:
                    # Market or lot-count changed — full graceful replace
                    if not is_stopping:
                        ss.replacement_strategy_slot_version_id = new_sv.id
                        ss.slot_state = "replacement_queued"
                    db.add(ss)
                    db.add(AuditLog(
                        actor_id=actor_id, actor_role="admin",
                        action="slot.replace_queued",
                        entity_type="session_slot", entity_id=ss.id,
                        detail=f"new_version={new_sv.id} alloc_changed={alloc_changed} market_changed={market_changed} deferred={is_stopping}",
                    ))
                    if engine and new_allocated > 0 and not is_stopping:
                        levels = new_sv.max_spread_ticks + 1
                        lot_size = round(new_allocated / levels, 4) if levels > 0 else 0.0
                        if lot_size > 0:
                            new_config = await _build_slot_market_config(new_sv, lot_size)
                            if new_config:
                                await engine.queue_slot_replacement(ss.id, new_config)
                elif alloc_changed:
                    # Only allocation changed — update capital live, no replace needed
                    db.add(ss)
                    db.add(AuditLog(
                        actor_id=actor_id, actor_role="admin",
                        action="slot.alloc_updated",
                        entity_type="session_slot", entity_id=ss.id,
                        detail=f"new_alloc_pct={new_alloc_pct} new_allocated={new_allocated}",
                    ))
                    if engine and new_allocated > 0 and not is_stopping:
                        await engine.update_slot_capital(ss.id, new_allocated)

            # ── Inject new slots (added in this publish) ─────────────────────
            for strategy_slot_id, sv in new_svs.items():
                if strategy_slot_id in existing_by_strategy_slot:
                    continue  # already handled above

                strat_slot = new_slots.get(strategy_slot_id)
                if not strat_slot:
                    continue

                allocated = round(deployable * strat_slot.allocation_pct / 100, 2)
                new_ss = DBSessionSlot(
                    id=str(_uuid_mod.uuid4()),
                    session_id=session.id,
                    strategy_slot_id=strategy_slot_id,
                    current_strategy_slot_version_id=sv.id,
                    slot_state="idle",  # runtime will set to active once started
                    allocation_pct=strat_slot.allocation_pct,
                    allocated_capital=allocated,
                )
                db.add(new_ss)
                await db.flush()

                if sv.polymarket_url:
                    db.add(SessionSlotMarketHistory(
                        session_slot_id=new_ss.id,
                        applied_strategy_version_id=version.id,
                        applied_strategy_slot_version_id=sv.id,
                        polymarket_url=sv.polymarket_url,
                        question=sv.question,
                        condition_id=sv.condition_id,
                        token_id_yes=sv.token_id_yes,
                        token_id_no=sv.token_id_no,
                        active_token_id=sv.token_id_yes if sv.side == "YES" else sv.token_id_no,
                        side=sv.side,
                    ))

                db.add(AuditLog(
                    actor_id=actor_id, actor_role="admin",
                    action="slot.injected",
                    entity_type="session_slot", entity_id=new_ss.id,
                    detail=f"new slot from publish version={version.id}",
                ))

                if engine and sv.polymarket_url and allocated > 0 and not is_stopping:
                    levels = sv.max_spread_ticks + 1
                    lot_size = round(allocated / levels, 4) if levels > 0 else 0.0
                    if lot_size > 0:
                        new_config = await _build_slot_market_config(sv, lot_size)
                        if new_config:
                            await engine.add_slot(new_ss.id, strategy_slot_id, new_config, allocated)

        await db.commit()


strategy_service = StrategyService()
