"""
user/profile.py — User profile endpoint.

GET /user/profile — returns bot stats + wallet stats (Polymarket API, 60s cache).
Response always has numeric values (never null) — fallback to DB values on API failure.
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func as _func

from app.core.deps import CurrentUser, DbSession
from app.db.models import User, Wallet, Session as DBSession, SessionSnapshot
from app.services.profile_service import profile_service
from app.runtime.session_registry import session_registry

router = APIRouter(prefix="/profile", tags=["user:profile"])


class ReferrerLookupOut(BaseModel):
    found: bool
    username: str


class AssignReferrerRequest(BaseModel):
    username: str = Field(min_length=3, max_length=15, pattern=r"^[A-Za-z0-9]+$")


class ReferralItemOut(BaseModel):
    user_id: str
    username: str
    reward_usdc: float


async def _find_user_by_username(db: DbSession, username: str) -> User | None:
    result = await db.execute(
        select(User).where(_func.lower(User.display_name) == username.lower())
    )
    return result.scalars().first()


def _referrer_lookup_error(target_user: User | None, current_user_id: str) -> HTTPException:
    if target_user and target_user.id == current_user_id:
        return HTTPException(status_code=400, detail="You cannot set yourself as referrer.")
    return HTTPException(status_code=404, detail="This username does not exist.")


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
            "referrer_username": None,
            "can_set_referrer": True,
            "referral_reward_usdc": 0.0,
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

    referrer_username = None
    if db_user.referrer_user_id:
        ref_q = await db.execute(select(User.display_name).where(User.id == db_user.referrer_user_id))
        referrer_username = ref_q.scalar()

    return {
        "trading_volume":    trading_volume,
        "bot_pnl":           bot_pnl,
        "fee_bps":           fee_bps,
        "fee_is_overridden": fee_is_overridden,
        "wallet_volume":     wallet_stats["wallet_volume"],
        "wallet_pnl":        wallet_stats["wallet_pnl"],
        "referrer_username": referrer_username,
        "can_set_referrer": db_user.referrer_user_id is None,
        "referral_reward_usdc": round(float(db_user.referral_reward_usdc or 0.0), 2),
    }


@router.get("/referrals", response_model=list[ReferralItemOut])
async def get_referrals(user: CurrentUser, db: DbSession):
    ref_q = await db.execute(
        select(User.id, User.display_name)
        .where(User.referrer_user_id == user["sub"])
        .order_by(_func.lower(_func.coalesce(User.display_name, "")), User.id)
    )
    referred_rows = ref_q.all()
    if not referred_rows:
        return []

    referred_ids = [row.id for row in referred_rows]
    session_q = await db.execute(
        select(DBSession.id, DBSession.user_id, DBSession.status)
        .where(DBSession.user_id.in_(referred_ids))
    )
    user_sessions = session_q.all()
    session_ids = [row.id for row in user_sessions]

    snap_by_session: dict[str, float] = {}
    if session_ids:
        snap_q = await db.execute(
            select(
                SessionSnapshot.session_id,
                _func.max(SessionSnapshot.paid_fees).label("fees"),
            )
            .where(SessionSnapshot.session_id.in_(session_ids))
            .group_by(SessionSnapshot.session_id)
        )
        for row in snap_q.all():
            snap_by_session[row.session_id] = float(row.fees or 0.0)

    live_engines = {engine.session_id: engine for engine in await session_registry.all()}
    reward_by_user: dict[str, float] = {user_id: 0.0 for user_id in referred_ids}

    for sess_id, referred_user_id, status in user_sessions:
        if status in ("running", "starting", "recovering"):
            engine = live_engines.get(sess_id)
            if engine:
                reward_by_user[referred_user_id] += float(engine.summary.paid_fees or 0.0) * 0.10
                continue
        reward_by_user[referred_user_id] += snap_by_session.get(sess_id, 0.0) * 0.10

    referrals = [
        ReferralItemOut(
            user_id=row.id,
            username=(row.display_name or "Unknown"),
            reward_usdc=round(reward_by_user.get(row.id, 0.0), 2),
        )
        for row in referred_rows
    ]
    referrals.sort(key=lambda item: (-item.reward_usdc, item.username.lower(), item.user_id))
    return referrals


@router.get("/referrer/lookup", response_model=ReferrerLookupOut)
async def lookup_referrer(
    user: CurrentUser,
    db: DbSession,
    username: str = Query(..., min_length=3, max_length=15, pattern=r"^[A-Za-z0-9]+$"),
):
    ref_user = await _find_user_by_username(db, username)
    if not ref_user or ref_user.id == user["sub"]:
        raise _referrer_lookup_error(ref_user, user["sub"])
    return {"found": True, "username": ref_user.display_name or username}


@router.post("/referrer")
async def assign_referrer(body: AssignReferrerRequest, user: CurrentUser, db: DbSession):
    db_user = await db.get(User, user["sub"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    if db_user.referrer_user_id:
        raise HTTPException(status_code=409, detail="Referrer already set")

    ref_user = await _find_user_by_username(db, body.username)
    if not ref_user or ref_user.id == db_user.id:
        raise _referrer_lookup_error(ref_user, db_user.id)

    db_user.referrer_user_id = ref_user.id
    await db.commit()

    return {
        "referrer_username": ref_user.display_name or body.username,
        "can_set_referrer": False,
    }
