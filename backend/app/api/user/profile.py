"""
user/profile.py — User profile endpoint.

GET /user/profile — returns bot stats + wallet stats (Polymarket API, 60s cache).
Response always has numeric values (never null) — fallback to DB values on API failure.
"""
from __future__ import annotations
from fastapi import APIRouter
from sqlalchemy import select, func as _func

from app.core.deps import CurrentUser, DbSession
from app.db.models import User, Wallet, Session as DBSession, SessionSnapshot
from app.services.profile_service import profile_service
from app.runtime.session_registry import session_registry

router = APIRouter(prefix="/profile", tags=["user:profile"])


@router.get("/")
async def get_profile(user: CurrentUser, db: DbSession):
    user_id = user["sub"]

    db_user = await db.get(User, user_id)
    if not db_user:
        return {
            "trading_volume":   0.0,
            "bot_pnl":          0.0,
            "fee_bps":          120,
            "fee_is_overridden": False,
            "wallet_volume":    0.0,
            "wallet_pnl":       0.0,
        }

    # Accurate stats from sessions (not User.trading_volume which can lag)
    sess_q = await db.execute(
        select(DBSession.id, DBSession.status).where(DBSession.user_id == user_id)
    )
    user_sessions = sess_q.all()
    session_ids = [r.id for r in user_sessions]

    live_engines = {e.session_id: e for e in await session_registry.all()}

    snap_q = await db.execute(
        select(
            SessionSnapshot.session_id,
            _func.max(SessionSnapshot.total_volume).label("vol"),
            _func.max(SessionSnapshot.realized_pnl).label("pnl"),
        )
        .where(SessionSnapshot.session_id.in_(session_ids))
        .group_by(SessionSnapshot.session_id)
    ) if session_ids else None
    snap_by_session = {}
    if snap_q:
        for r in snap_q.all():
            snap_by_session[r.session_id] = (float(r.vol or 0), float(r.pnl or 0))

    trading_volume = 0.0
    bot_pnl = 0.0
    for sess_id, status in user_sessions:
        if status in ("running", "starting", "recovering"):
            eng = live_engines.get(sess_id)
            if eng:
                sm = eng.summary
                trading_volume += sm.total_volume or 0.0
                bot_pnl        += sm.realized_pnl or 0.0
                continue
        vol, pnl = snap_by_session.get(sess_id, (0.0, 0.0))
        trading_volume += vol
        bot_pnl        += pnl

    trading_volume = round(trading_volume, 2)
    bot_pnl        = round(bot_pnl, 2)

    fee_is_overridden = db_user.fee_override_bps is not None
    # Send raw DB value (bps×10). Frontend converts for display.
    fee_bps = db_user.fee_override_bps if fee_is_overridden else (db_user.effective_fee_bps or 120)

    # Primary wallet for Polymarket leaderboard lookup
    w_q = await db.execute(
        select(Wallet)
        .where(Wallet.user_id == user_id, Wallet.is_primary == True)
        .limit(1)
    )
    wallet = w_q.scalars().first()
    proxy_address = wallet.proxy_address if wallet else None

    wallet_stats = {"wallet_volume": trading_volume, "wallet_pnl": bot_pnl}
    if proxy_address:
        wallet_stats = await profile_service.get_wallet_stats(
            user_id=user_id,
            proxy_address=proxy_address,
            fallback_volume=trading_volume,
            fallback_pnl=bot_pnl,
        )

    return {
        "trading_volume":    trading_volume,
        "bot_pnl":           bot_pnl,
        "fee_bps":           fee_bps,
        "fee_is_overridden": fee_is_overridden,
        "wallet_volume":     wallet_stats["wallet_volume"],
        "wallet_pnl":        wallet_stats["wallet_pnl"],
    }
