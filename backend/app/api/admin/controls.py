"""
admin/controls.py — Global controls (pause, audit log).
"""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, update

from app.core.deps import AdminUser, DbSession
from app.db.models import Strategy, AuditLog
from app.runtime.session_registry import session_registry

router = APIRouter(prefix="/admin/controls", tags=["admin:controls"])


class GlobalPauseRequest(BaseModel):
    strategy_id: str
    paused: bool


@router.post("/global-pause")
async def set_global_pause(body: GlobalPauseRequest, db: DbSession, admin: AdminUser):
    """
    Global pause:
    - No new sessions
    - Active sessions stop opening new entries
    - Existing exposure exits normally
    """
    await db.execute(
        update(Strategy)
        .where(Strategy.id == body.strategy_id)
        .values(global_pause=body.paused)
    )
    db.add(AuditLog(
        actor_id=admin["sub"], actor_role="admin",
        action="global.pause" if body.paused else "global.unpause",
        entity_type="strategy", entity_id=body.strategy_id,
    ))
    await db.commit()

    # Propagate to running sessions
    if body.paused:
        engines = await session_registry.all()
        for engine in engines:
            await engine.soft_stop()

    return {"ok": True, "paused": body.paused}


@router.get("/audit")
async def get_audit_log(
    db: DbSession, admin: AdminUser,
    entity_type: str = None,
    entity_id: str = None,
    limit: int = 100,
):
    q = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if entity_type:
        q = q.where(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditLog.entity_id == entity_id)
    result = await db.execute(q)
    return [
        {
            "id": a.id,
            "actor_id": a.actor_id,
            "actor_role": a.actor_role,
            "action": a.action,
            "entity_type": a.entity_type,
            "entity_id": a.entity_id,
            "detail": a.detail,
            "created_at": a.created_at.isoformat(),
        }
        for a in result.scalars().all()
    ]
