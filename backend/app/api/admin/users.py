"""
admin/users.py — Admin user management.

GET  /admin/users/         — list all users with live balances (from in-memory cache)
PATCH /admin/users/{id}    — update fee_override_bps / display_name
POST /admin/users/{id}/stop-session — soft/hard stop a user's active session
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, and_
from typing import Optional

from app.core.deps import AdminUser, DbSession
from app.db.models import User, Wallet, Session as DBSession, AuditLog
from app.runtime.session_registry import session_registry
from app.runtime.session_manager import session_manager
from app.services.balance_poller import balance_poller

router = APIRouter(prefix="/admin/users", tags=["admin:users"])


class PatchUserRequest(BaseModel):
    # fee_override_bps: >=0 to set override, -1 to clear
    fee_override_bps: Optional[int] = Field(None, ge=-1, le=10_000)
    display_name: Optional[str] = None


class StopSessionRequest(BaseModel):
    mode: str = "soft"   # "soft" | "hard"


@router.get("/")
async def list_users(db: DbSession, admin: AdminUser, limit: int = 100, offset: int = 0):
    # Wait for first cache population if cold (first boot only)
    if not balance_poller._ready.is_set():
        await balance_poller.wait_ready()

    # Single JOIN: users + primary wallets
    rows = await db.execute(
        select(User, Wallet)
        .outerjoin(Wallet, and_(Wallet.user_id == User.id, Wallet.is_primary == True))
        .order_by(User.created_at.desc())
        .limit(limit)
        .offset(offset)
        # No is_active filter — admin sees all users
    )
    pairs = rows.all()   # [(User, Wallet | None), ...]

    user_ids = [u.id for u, _ in pairs]

    # Active sessions in one query
    sessions_q = await db.execute(
        select(DBSession).where(
            DBSession.user_id.in_(user_ids),
            DBSession.status.in_(["pending", "starting", "running", "recovering"]),
        )
    )
    sessions_by_user: dict[str, DBSession] = {}
    for s in sessions_q.scalars().all():
        sessions_by_user.setdefault(s.user_id, s)

    result = []
    for u, wallet in pairs:
        bal = balance_poller.get(u.id)
        active_session = sessions_by_user.get(u.id)
        session_live = False
        session_status = None
        if active_session:
            engine = await session_registry.get(active_session.id)
            session_live = engine is not None
            session_status = active_session.status

        result.append({
            "id":                  u.id,
            "display_name":        u.display_name,
            "is_admin":            u.is_admin,
            "is_active":           u.is_active,
            "fee_override_bps":    u.fee_override_bps,
            "effective_fee_bps":   u.effective_fee_bps,
            "trading_volume":      u.trading_volume,
            "bot_pnl":             u.bot_pnl,
            "wallet_address":      wallet.wallet_address if wallet else None,
            "proxy_address":       wallet.proxy_address if wallet else None,
            "onboarding_complete": wallet.onboarding_complete if wallet else False,
            "wallet_usdc":         bal.wallet_usdc,
            "wallet_eth":          bal.wallet_eth,
            "trading_balance":     bal.trading_balance,
            "active_session_id":   active_session.id if active_session else None,
            "session_status":      session_status,
            "session_live":        session_live,
            "created_at":          u.created_at.isoformat(),
        })
    return result


@router.patch("/{user_id}")
async def patch_user(user_id: str, body: PatchUserRequest, db: DbSession, admin: AdminUser):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    changed = []
    new_fee_for_engine: Optional[int] = None
    new_override_flag: Optional[bool] = None

    if body.fee_override_bps is not None:
        if body.fee_override_bps == -1:
            user.fee_override_bps = None
            new_fee_for_engine = user.effective_fee_bps
            new_override_flag  = False
            changed.append("fee_override_bps=cleared")
        else:
            user.fee_override_bps = body.fee_override_bps
            new_fee_for_engine = body.fee_override_bps
            new_override_flag  = True
            changed.append(f"fee_override_bps={body.fee_override_bps}")

    if body.display_name is not None:
        user.display_name = body.display_name
        changed.append(f"display_name={body.display_name!r}")

    if not changed:
        return {"ok": True, "changed": []}

    db.add(AuditLog(
        actor_id=admin["sub"], actor_role="admin",
        action="user.patch", entity_type="user", entity_id=user_id,
        detail=", ".join(changed),
    ))
    await db.commit()

    # Push fee change to live session engine immediately (zero restart)
    if new_fee_for_engine is not None:
        s_q = await db.execute(
            select(DBSession).where(
                DBSession.user_id == user_id,
                DBSession.status.in_(["running", "starting", "recovering"]),
            ).limit(1)
        )
        active = s_q.scalars().first()
        if active:
            engine = await session_registry.get(active.id)
            if engine:
                engine._user_fee_bps      = new_fee_for_engine
                engine._fee_is_overridden = new_override_flag

    return {"ok": True, "changed": changed}


@router.post("/{user_id}/stop-session")
async def stop_user_session(user_id: str, body: StopSessionRequest, db: DbSession, admin: AdminUser):
    s_q = await db.execute(
        select(DBSession).where(
            DBSession.user_id == user_id,
            DBSession.status.in_(["pending", "starting", "running", "recovering"]),
        ).limit(1)
    )
    active = s_q.scalars().first()
    if not active:
        raise HTTPException(404, "No active session for this user")

    actor_id = admin["sub"]
    await session_manager.stop_session(active.id, mode=body.mode, actor_id=actor_id)

    db.add(AuditLog(
        actor_id=actor_id, actor_role="admin",
        action=f"session.{body.mode}_stop", entity_type="session", entity_id=active.id,
        detail=f"admin forced stop for user {user_id}",
    ))
    await db.commit()

    return {"ok": True, "session_id": active.id, "mode": body.mode}
