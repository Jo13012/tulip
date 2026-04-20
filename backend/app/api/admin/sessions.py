"""
admin/sessions.py — Admin session inspector + controls.
"""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import AdminUser, DbSession
from app.db.models import Session as DBSession, SessionSlot, SessionSlotMarketHistory, AuditLog, User
from app.runtime.session_registry import session_registry
from app.runtime.session_manager import session_manager
from app.services.recovery_service import recovery_service

router = APIRouter(prefix="/admin/sessions", tags=["admin:sessions"])


@router.get("/")
async def list_sessions(db: DbSession, admin: AdminUser, limit: int = 50, offset: int = 0):
    from app.db.models import SessionSnapshot
    from sqlalchemy import func as _func

    sessions_q = await db.execute(select(DBSession).order_by(DBSession.created_at.desc()).limit(limit).offset(offset))
    sessions = sessions_q.scalars().all()

    user_ids = list({s.user_id for s in sessions if s.user_id})
    users_q = await db.execute(select(User).where(User.id.in_(user_ids)))
    users_by_id = {u.id: u for u in users_q.scalars().all()}

    snap_q = await db.execute(
        select(SessionSnapshot.session_id, _func.max(SessionSnapshot.total_volume).label("max_vol"))
        .group_by(SessionSnapshot.session_id)
    )
    snap_vols = {row.session_id: float(row.max_vol or 0) for row in snap_q.all()}

    # Single registry snapshot — one lock acquisition
    live_engines = {e.session_id: e for e in await session_registry.all()}

    result = []
    for s in sessions:
        engine = live_engines.get(s.id)
        if engine:
            total_volume = engine.total_volume or 0.0
        else:
            total_volume = snap_vols.get(s.id, 0.0)
        u = users_by_id.get(s.user_id)
        result.append({
            "session_id":    s.id,
            "user_id":       s.user_id,
            "username":      u.display_name if u and u.display_name else None,
            "status":        s.status,
            "health_state":  s.health_state,
            "gross_capital": s.session_gross_capital,
            "total_volume":  round(total_volume, 2),
            "stop_mode":     s.stop_mode,
            "started_at":    s.started_at.isoformat() if s.started_at else None,
            "stopped_at":    s.stopped_at.isoformat() if s.stopped_at else None,
            "runtime_live":  engine is not None,
            "stop_reason":   s.stop_reason,
        })
    return result


@router.get("/{session_id}")
async def get_session(session_id: str, db: DbSession, admin: AdminUser):
    session = await db.get(DBSession, session_id)
    if not session:
        raise HTTPException(404)

    slots_q = await db.execute(select(SessionSlot).where(SessionSlot.session_id == session_id))
    slots = slots_q.scalars().all()
    mh_q = await db.execute(
        select(SessionSlotMarketHistory)
        .join(SessionSlot)
        .where(SessionSlot.session_id == session_id)
        .order_by(SessionSlotMarketHistory.started_at.desc())
    )
    all_mh = mh_q.scalars().all()

    # Group market history by slot id
    mh_by_slot: dict[str, list] = {}
    for m in all_mh:
        mh_by_slot.setdefault(m.session_slot_id, []).append(m)

    engine = (await session_registry.all())
    engine_map = {e.session_id: e for e in engine}
    eng = engine_map.get(session_id)

    slot_data = [
        {
            "session_slot_id":  ss.id,
            "strategy_slot_id": ss.strategy_slot_id,
            "slot_state":       ss.slot_state,
            "allocation_pct":   ss.allocation_pct,
            "allocated_capital": ss.allocated_capital,
            "runtime":          eng.slot_summary(ss.id) if eng else None,
            "market_history": [
                {
                    "url":        m.polymarket_url,
                    "question":   m.question,
                    "started_at": m.started_at.isoformat(),
                    "ended_at":   m.ended_at.isoformat() if m.ended_at else None,
                    "volume":     m.volume,
                    "pnl":        m.realized_pnl,
                }
                for m in mh_by_slot.get(ss.id, [])
            ],
        }
        for ss in slots
    ]

    return {
        "session_id":        session.id,
        "user_id":           session.user_id,
        "status":            session.status,
        "health_state":      session.health_state,
        "gross_capital":     session.session_gross_capital,
        "deployable_capital": session.session_deployable_capital,
        "fee_reserve":       session.session_fee_reserve,
        "stop_mode":         session.stop_mode,
        "stop_reason":       session.stop_reason,
        "started_at":        session.started_at.isoformat() if session.started_at else None,
        "stopped_at":        session.stopped_at.isoformat() if session.stopped_at else None,
        "slots":             slot_data,
        "runtime":           eng.summary.model_dump() if eng else None,
        "logs":              eng.recent_logs(200) if eng else [],
    }


class StopSessionRequest(BaseModel):
    mode: str = "hard"  # "soft" | "hard"

@router.post("/{session_id}/stop")
async def stop_session(session_id: str, body: StopSessionRequest, admin: AdminUser, db: DbSession):
    await session_manager.stop_session(session_id, mode=body.mode, actor_id=admin["sub"])
    db.add(AuditLog(
        actor_id=admin["sub"], actor_role="admin",
        action=f"session.{body.mode}_stop", entity_type="session", entity_id=session_id,
        detail=f"admin {body.mode} stop",
    ))
    await db.commit()
    return {"ok": True, "mode": body.mode}

@router.post("/{session_id}/force-stop")
async def force_stop(session_id: str, admin: AdminUser):
    await session_manager.stop_session(session_id, mode="hard", actor_id=admin["sub"])
    return {"ok": True}


@router.post("/{session_id}/retry-recovery")
async def retry_recovery(session_id: str, admin: AdminUser):
    import asyncio
    asyncio.create_task(recovery_service._recover_session(session_id))
    return {"ok": True, "message": "Recovery queued"}


class AdminLaunchSessionRequest(BaseModel):
    user_id: str
    wallet_id: str
    gross_capital: float
    stop_mode: str               # "days" | "volume"
    stop_days: int | None = None
    target_volume: float | None = None
    session_sl_pct: float = 20.0


@router.post("/launch-for-user")
async def admin_launch_for_user(body: AdminLaunchSessionRequest, admin: AdminUser, db: DbSession):
    from sqlalchemy import select as _sel
    from app.db.models import Strategy as DBStrategy

    # Ensure user has no running session
    existing_q = await db.execute(
        _sel(DBSession).where(
            DBSession.user_id == body.user_id,
            DBSession.status.in_(["pending", "starting", "running", "recovering"]),
        ).limit(1)
    )
    if existing_q.scalars().first():
        raise HTTPException(400, "User already has an active session")

    # Resolve global strategy
    strat_q = await db.execute(_sel(DBStrategy).order_by(DBStrategy.created_at.asc()).limit(1))
    strat = strat_q.scalars().first()
    if not strat:
        raise HTTPException(503, "No published strategy available")

    session = await session_manager.launch_session(
        user_id=body.user_id,
        wallet_id=body.wallet_id,
        strategy_id=strat.id,
        gross_capital=body.gross_capital,
        stop_mode=body.stop_mode,
        stop_days=body.stop_days,
        target_volume=body.target_volume,
        session_sl_pct=body.session_sl_pct,
    )

    db.add(AuditLog(
        actor_id=admin["sub"], actor_role="admin",
        action="session.admin_launch", entity_type="session", entity_id=session.id,
        detail=f"admin launched for user {body.user_id} capital={body.gross_capital}",
    ))
    await db.commit()

    return {"session_id": session.id, "status": session.status}
