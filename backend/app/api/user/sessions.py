"""
user/sessions.py — User session management endpoints.
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.db.models import Session as DBSession, Wallet, Strategy, StrategyVersion, SessionSnapshot
from app.runtime.session_registry import session_registry
from app.runtime.session_manager import session_manager, ViabilityResult

router = APIRouter(prefix="/sessions", tags=["user:sessions"])


class LaunchSessionRequest(BaseModel):
    wallet_id: str
    gross_capital: float
    stop_mode: str              # "days" | "volume"
    stop_days: Optional[int]    = None
    target_volume: Optional[float] = None
    session_sl_pct: float       = 20.0


class StopSessionRequest(BaseModel):
    mode: str = "soft"   # "soft" | "hard"


class ViabilityRequest(BaseModel):
    gross_capital: float


async def _get_global_strategy_id(db) -> str:
    """Return the single global strategy id. Raises if none published."""
    q = await db.execute(
        select(Strategy).order_by(Strategy.created_at.asc()).limit(1)
    )
    strat = q.scalars().first()
    if not strat:
        raise HTTPException(status_code=503, detail="No published strategy available")
    return strat.id


@router.post("/viability")
async def check_viability(body: ViabilityRequest, user: CurrentUser, db: DbSession):
    strategy_id = await _get_global_strategy_id(db)
    result = await session_manager.check_viability(strategy_id, body.gross_capital)
    return {
        "viable":             result.viable,
        "min_capital":        result.min_capital,
        "recommended_capital": result.recommended_capital,
        "reason":             result.reason,
    }


@router.post("/launch")
async def launch_session(body: LaunchSessionRequest, user: CurrentUser, db: DbSession):
    user_id = user["sub"]
    strategy_id = await _get_global_strategy_id(db)
    try:
        session = await session_manager.launch_session(
            user_id=user_id,
            wallet_id=body.wallet_id,
            strategy_id=strategy_id,
            gross_capital=body.gross_capital,
            stop_mode=body.stop_mode,
            stop_days=body.stop_days,
            target_volume=body.target_volume,
            session_sl_pct=body.session_sl_pct,
        )
        return {"session_id": session.id, "status": session.status}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/active")
async def get_active_session(user: CurrentUser, db: DbSession):
    user_id = user["sub"]
    q = await db.execute(
        select(DBSession).where(
            DBSession.user_id == user_id,
            DBSession.status.in_(["pending", "starting", "running", "stopping", "recovering"]),
        )
    )
    session = q.scalars().first()
    if not session:
        return None

    engine = await session_registry.get(session.id)
    rt_summary = engine.summary.model_dump() if engine else None

    # Prefer live engine state over stale DB state
    live_status      = engine.status       if engine else session.status
    live_health      = engine.health_state.value if engine and hasattr(engine.health_state, 'value') \
                       else (engine.health_state if engine else session.health_state)

    return {
        "session_id":  session.id,
        "wallet_id":   session.wallet_id,
        "status":      live_status,
        "health_state": live_health,
        "gross_capital": session.session_gross_capital,
        "deployable_capital": session.session_deployable_capital,
        "fee_reserve": session.session_fee_reserve,
        "stop_mode":   session.stop_mode,
        "stop_days":   session.stop_days,
        "target_volume": session.target_volume,
        "session_sl_pct": session.session_sl_pct,
        "started_at":  session.started_at.isoformat() if session.started_at else None,
        "runtime":     rt_summary,
    }


@router.post("/{session_id}/stop")
async def stop_session(session_id: str, body: StopSessionRequest, user: CurrentUser):
    user_id = user["sub"]
    # Verify session belongs to user
    engine = await session_registry.get(session_id)
    if engine and engine.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your session")

    await session_manager.stop_session(session_id, mode=body.mode, actor_id=user_id)
    return {"ok": True}


@router.get("/history")
async def get_session_history(user: CurrentUser, db: DbSession):
    from sqlalchemy import text
    user_id = user["sub"]
    q = await db.execute(
        select(DBSession)
        .where(DBSession.user_id == user_id)
        .order_by(DBSession.created_at.desc())
        .limit(50)
    )
    sessions = q.scalars().all()
    if not sessions:
        return []

    # Latest snapshot per session for vol/pnl/fees
    session_ids = [s.id for s in sessions]
    snap_q = await db.execute(
        select(SessionSnapshot)
        .where(SessionSnapshot.session_id.in_(session_ids))
        .order_by(SessionSnapshot.session_id, SessionSnapshot.snapshot_at.desc())
        .distinct(SessionSnapshot.session_id)
    )
    snaps = {sn.session_id: sn for sn in snap_q.scalars().all()}

    result = []
    for s in sessions:
        sn = snaps.get(s.id)
        # For running sessions, pull live data from engine
        engine = await session_registry.get(s.id)
        rt = engine.summary if engine else None
        result.append({
            "session_id":    s.id,
            "status":        s.status,
            "health_state":  s.health_state,
            "gross_capital": s.session_gross_capital,
            "stop_mode":     s.stop_mode,
            "stop_days":     s.stop_days,
            "target_volume": s.target_volume,
            "started_at":    s.started_at.isoformat() if s.started_at else None,
            "stopped_at":    s.stopped_at.isoformat() if s.stopped_at else None,
            "stop_reason":   s.stop_reason,
            "total_volume":  rt.total_volume if rt else (sn.total_volume if sn else 0.0),
            "realized_pnl":  rt.realized_pnl if rt else (sn.realized_pnl if sn else 0.0),
            "unrealized_pnl": rt.unrealized_pnl if rt else (sn.unrealized_pnl if sn else 0.0),
            "paid_fees":     rt.paid_fees if rt else (sn.paid_fees if sn else 0.0),
            "trade_count":   rt.trade_count if rt else 0,
            "order_count":   rt.order_count if rt else 0,
        })
    return result
