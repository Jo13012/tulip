"""
admin/users.py — Admin user management.

GET  /admin/users/         — list all users with live balances (from in-memory cache)
PATCH /admin/users/{id}    — update fee_override_bps / display_name
POST /admin/users/{id}/stop-session — soft/hard stop a user's active session
GET  /admin/users/{id}/poly-state   — fetch live Poly open orders + token balances
POST /admin/users/{id}/nuclear-close — cancel orders and/or taker-sell positions on Poly
"""
from __future__ import annotations

import asyncio
import math
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, and_
from typing import Optional, List

from app.core.deps import AdminUser, DbSession
from app.db.models import User, Wallet, Session as DBSession, AuditLog, SessionSnapshot
from app.runtime.session_registry import session_registry
from app.runtime.session_manager import session_manager
from app.services.balance_poller import balance_poller
from app.integrations.polymarket.execution_client import ExecutionClient
from app.runtime.market_data_service import market_data_service
from app.runtime.runtime_models import norm
from app.services.nuclear_close_service import run_nuclear_close as _run_nuclear_close
from sqlalchemy import func as _func


async def _user_accurate_stats(db, user_ids: list[str], live_engines: dict) -> dict[str, dict]:
    """
    Compute accurate total_volume + bot_pnl for each user_id from:
    1. max(SessionSnapshot.total_volume) / max(realized_pnl) per stopped session
    2. live engine summary for running sessions
    Returns {user_id: {"volume": float, "pnl": float}}
    """
    if not user_ids:
        return {}

    # All sessions for these users
    sess_q = await db.execute(
        select(DBSession.id, DBSession.user_id, DBSession.status)
        .where(DBSession.user_id.in_(user_ids))
    )
    sessions = sess_q.all()  # (id, user_id, status)
    session_ids = [r.id for r in sessions]
    session_user = {r.id: r.user_id for r in sessions}

    # Best snapshot per session
    snap_q = await db.execute(
        select(
            SessionSnapshot.session_id,
            _func.max(SessionSnapshot.total_volume).label("vol"),
            _func.max(SessionSnapshot.realized_pnl).label("pnl"),
        )
        .where(SessionSnapshot.session_id.in_(session_ids))
        .group_by(SessionSnapshot.session_id)
    )
    snap_by_session = {r.session_id: (float(r.vol or 0), float(r.pnl or 0)) for r in snap_q.all()}

    stats: dict[str, dict] = {uid: {"volume": 0.0, "pnl": 0.0} for uid in user_ids}

    for sess_id, user_id, status in sessions:
        if status in ("running", "starting", "recovering"):
            eng = live_engines.get(sess_id)
            if eng:
                sm = eng.summary
                stats[user_id]["volume"] += sm.total_volume or 0.0
                stats[user_id]["pnl"]    += sm.realized_pnl or 0.0
                continue
        # stopped or no live engine — use snapshot
        vol, pnl = snap_by_session.get(sess_id, (0.0, 0.0))
        stats[user_id]["volume"] += vol
        stats[user_id]["pnl"]    += pnl

    return stats

router = APIRouter(prefix="/admin/users", tags=["admin:users"])


class PatchUserRequest(BaseModel):
    # fee_override_bps: >=0 to set override, -1 to clear
    fee_override_bps: Optional[int] = Field(None, ge=-1, le=10_000)
    display_name: Optional[str] = None
    # free_fee_days: >0 to grant N days free fee from now, -1 to cancel
    free_fee_days: Optional[int] = Field(None, ge=-1, le=365)


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

    live_engines = {e.session_id: e for e in await session_registry.all()}
    accurate = await _user_accurate_stats(db, user_ids, live_engines)

    result = []
    for u, wallet in pairs:
        bal = balance_poller.get(u.id)
        active_session = sessions_by_user.get(u.id)
        session_live = False
        session_status = None
        if active_session:
            session_live = active_session.id in live_engines
            session_status = active_session.status

        us = accurate.get(u.id, {"volume": 0.0, "pnl": 0.0})
        result.append({
            "id":                  u.id,
            "display_name":        u.display_name,
            "is_admin":            u.is_admin,
            "is_active":           u.is_active,
            "fee_override_bps":    u.fee_override_bps,
            "effective_fee_bps":   u.effective_fee_bps,
            "trading_volume":      round(us["volume"], 2),
            "bot_pnl":             round(us["pnl"], 2),
            "wallet_address":      wallet.wallet_address if wallet else None,
            "wallet_id":           wallet.id if wallet else None,
            "proxy_address":       wallet.proxy_address if wallet else None,
            "onboarding_complete": wallet.onboarding_complete if wallet else False,
            "onboarding_completed_at": wallet.onboarding_completed_at.isoformat() if wallet and wallet.onboarding_completed_at else None,
            "wallet_usdc":         bal.wallet_usdc,
            "wallet_eth":          bal.wallet_eth,
            "trading_balance":     bal.trading_balance,
            "active_session_id":   active_session.id if active_session else None,
            "session_status":      session_status,
            "session_live":        session_live,
            "created_at":          u.created_at.isoformat(),
            "free_fee_ends_at":    u.free_fee_ends_at.isoformat() if u.free_fee_ends_at else None,
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
    clear_free_fee_ends_at: bool = False

    if body.fee_override_bps is not None:
        if body.fee_override_bps == -1:
            user.fee_override_bps = None
            user.free_fee_ends_at = None  # cancel any pending auto-expiry
            new_fee_for_engine = user.effective_fee_bps
            new_override_flag  = False
            clear_free_fee_ends_at = True
            changed.append("fee_override_bps=cleared")
        else:
            user.fee_override_bps = body.fee_override_bps
            new_fee_for_engine = body.fee_override_bps
            new_override_flag  = True
            changed.append(f"fee_override_bps={body.fee_override_bps}")

    new_free_fee_ends_at = None  # for engine push
    if body.free_fee_days is not None:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        if body.free_fee_days == -1:
            user.free_fee_ends_at = None
            user.fee_override_bps = None
            new_fee_for_engine = user.effective_fee_bps
            new_override_flag  = False
            clear_free_fee_ends_at = True
            changed.append("free_fee=cancelled")
        else:
            ends_at = _dt.now(_tz.utc) + _td(days=body.free_fee_days)
            user.free_fee_ends_at = ends_at
            user.fee_override_bps = 0
            new_free_fee_ends_at  = ends_at
            new_fee_for_engine    = 0
            new_override_flag     = True
            changed.append(f"free_fee={body.free_fee_days}d")

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
                if clear_free_fee_ends_at:
                    engine._free_fee_ends_at = None
                if new_free_fee_ends_at is not None:
                    engine._free_fee_ends_at = new_free_fee_ends_at

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


async def _get_user_client(user_id: str, db) -> ExecutionClient:
    """Build and initialize an ExecutionClient for the user's primary wallet."""
    w_q = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id, Wallet.is_primary == True).limit(1)
    )
    wallet = w_q.scalars().first()
    if not wallet or not wallet.privy_wallet_id:
        raise HTTPException(400, "No primary wallet / privy wallet not linked")
    client = ExecutionClient(
        privy_wallet_id=wallet.privy_wallet_id,
        wallet_address=wallet.wallet_address,
        proxy_address=wallet.proxy_address or "",
    )
    await client.initialize()
    return client


@router.get("/{user_id}/poly-state")
async def get_poly_state(user_id: str, db: DbSession, admin: AdminUser):
    """
    Returns the live Poly state for this user:
    - open_orders: list of {order_id, token_id, side, price, size_remaining}
    - positions: list of {token_id, balance} for any token_id in DB slots + open orders
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    client = await _get_user_client(user_id, db)
    try:
        # Fetch open orders + all positions concurrently
        orders_task    = asyncio.create_task(client.get_open_orders())
        positions_task = asyncio.create_task(client.get_all_positions())
        live_orders, raw_positions = await asyncio.gather(orders_task, positions_task, return_exceptions=False)
        if not isinstance(live_orders, dict):
            live_orders = {}
        if not isinstance(raw_positions, list):
            raw_positions = []

        # token_id → question from positions data-api response
        token_question: dict[str, str] = {
            pos["token_id"]: pos.get("question") or ""
            for pos in raw_positions if pos.get("token_id")
        }

        open_orders_list = []
        for oid, order in live_orders.items():
            orig      = float(order.get("original_size") or 0)
            matched   = float(order.get("size_matched") or 0)
            remaining = max(0.0, orig - matched)
            if remaining < 0.001:
                continue
            tid = order.get("asset_id") or ""
            open_orders_list.append({
                "order_id":       oid,
                "token_id":       tid,
                "question":       token_question.get(tid, ""),
                "side":           (order.get("side") or "").upper(),
                "price":          float(order.get("price") or 0),
                "size_remaining": round(remaining, 4),
            })

        all_tids = list({p["token_id"] for p in raw_positions if p.get("token_id")})

        # Fetch midpoints + Gamma closed status concurrently
        async def fetch_mid(tid: str):
            try:
                mid = await market_data_service.get_midpoint(tid)
                return tid, mid or 0.0
            except Exception:
                return tid, 0.0

        async def fetch_gamma_closed(tids: list[str]) -> dict[str, bool]:
            if not tids:
                return {}
            try:
                import httpx as _httpx
                # Gamma accepts comma-separated clobTokenIds
                params = "&".join(f"clobTokenIds={t}" for t in tids)
                async with _httpx.AsyncClient(timeout=10.0, trust_env=False) as h:
                    r = await h.get(f"{settings.GAMMA_HOST}/markets?{params}")
                    if not r.is_success:
                        return {}
                    markets = r.json()
                    if not isinstance(markets, list):
                        return {}
                    result: dict[str, bool] = {}
                    for m in markets:
                        closed = bool(m.get("closed"))
                        raw_ids = m.get("clobTokenIds") or "[]"
                        if isinstance(raw_ids, str):
                            import json as _json
                            try:
                                raw_ids = _json.loads(raw_ids)
                            except Exception:
                                raw_ids = []
                        for tid in (raw_ids if isinstance(raw_ids, list) else []):
                            result[str(tid)] = closed
                    return result
            except Exception:
                return {}

        mid_results, closed_map = await asyncio.gather(
            asyncio.gather(*[fetch_mid(tid) for tid in all_tids]),
            fetch_gamma_closed(all_tids),
        )
        mids = dict(mid_results)

        positions_list = []
        for pos in raw_positions:
            tid = pos.get("token_id") or ""
            bal = float(pos.get("size") or 0)
            if bal < 0.01 or not tid:
                continue
            cur = float(pos.get("cur_price") or 0)
            mid = mids.get(tid, cur)
            price = float(mid) if float(mid) > 0 else cur
            resolved = closed_map.get(tid, False)
            positions_list.append({
                "token_id":  tid,
                "question":  (pos.get("question") or "").strip(),
                "outcome":   pos.get("outcome") or "",
                "balance":   round(bal, 4),
                "mid":       round(price, 4),
                "value_usd": round(bal * price, 2),
                "resolved":  resolved,
            })

        total_value_usd = round(sum(p["value_usd"] for p in positions_list), 2)

        return {
            "open_orders":     open_orders_list,
            "positions":       positions_list,
            "total_value_usd": total_value_usd,
        }
    finally:
        await client.close()


class NuclearCloseRequest(BaseModel):
    cancel_orders: bool = True
    cancel_order_ids: List[str] = Field(default_factory=list)  # specific order IDs; if set, only these are cancelled
    sell_token_ids: List[str] = Field(default_factory=list)


@router.post("/{user_id}/nuclear-close")
async def nuclear_close(user_id: str, body: NuclearCloseRequest, db: DbSession, admin: AdminUser):
    """
    Cancel all open orders (if cancel_orders=True) and/or taker-sell
    the specified token positions at current best bid on Poly.
    Works regardless of session state — operates directly on Poly.
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    if not body.cancel_orders and not body.cancel_order_ids and not body.sell_token_ids:
        raise HTTPException(400, "Nothing to do")

    client = await _get_user_client(user_id, db)
    try:
        results = await _run_nuclear_close(
            client,
            cancel_orders=body.cancel_orders,
            cancel_order_ids=body.cancel_order_ids or None,
            sell_token_ids=body.sell_token_ids or None,
            sell_all_positions=not body.sell_token_ids,
        )

        db.add(AuditLog(
            actor_id=admin["sub"], actor_role="admin",
            action="user.nuclear_close", entity_type="user", entity_id=user_id,
            detail=(
                f"cancelled={len(results['cancelled_orders'])} "
                f"sold={len(results['sold_positions'])} "
                f"redeemed={len(results.get('redeemed_positions', []))} "
                f"errors={len(results['errors'])}"
            ),
        ))
        await db.commit()
    finally:
        await client.close()

    return {"ok": True, **results}
