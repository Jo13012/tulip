"""
admin/analytics.py — Admin analytics endpoints.
"""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter
from sqlalchemy import select

from app.core.deps import AdminUser, DbSession
from app.services.analytics_service import analytics_service

router = APIRouter(prefix="/admin/analytics", tags=["admin:analytics"])


@router.get("/global")
async def global_metrics(db: DbSession, admin: AdminUser):
    return await analytics_service.global_metrics(db)


@router.get("/global-strategy")
async def global_strategy_analytics(db: DbSession, admin: AdminUser):
    """Analytics for the one global strategy — auto-resolves the strategy ID."""
    from sqlalchemy import select as _sel
    from app.db.models import Strategy
    q = await db.execute(
        _sel(Strategy).order_by(Strategy.created_at.asc()).limit(1)
    )
    strat = q.scalars().first()
    if not strat:
        return {"summary": None, "versions": [], "markets": []}
    summary  = await analytics_service.strategy_summary(db, strat.id)
    versions = await analytics_service.version_performance(db, strat.id)
    markets  = await analytics_service.market_history_performance(db, None)

    return {"summary": summary, "versions": versions, "markets": markets}


@router.get("/strategy/{strategy_id}")
async def strategy_analytics(strategy_id: str, db: DbSession, admin: AdminUser):
    return await analytics_service.strategy_summary(db, strategy_id)


@router.get("/strategy/{strategy_id}/versions")
async def version_analytics(strategy_id: str, db: DbSession, admin: AdminUser):
    return await analytics_service.version_performance(db, strategy_id)


@router.get("/slot/{strategy_slot_id}")
async def slot_analytics(strategy_slot_id: str, db: DbSession, admin: AdminUser):
    return await analytics_service.slot_performance(db, strategy_slot_id)


@router.get("/markets")
async def market_analytics(db: DbSession, admin: AdminUser, strategy_slot_id: Optional[str] = None):
    return await analytics_service.market_history_performance(db, strategy_slot_id)


@router.get("/daily-volume")
async def daily_volume(db: DbSession, admin: AdminUser, days: int = 60):
    return await analytics_service.daily_volume(db, days)


@router.get("/daily-cpm")
async def daily_cpm(db: DbSession, admin: AdminUser, days: int = 60):
    return await analytics_service.daily_cpm(db, days)


@router.get("/daily-multiple")
async def daily_multiple(db: DbSession, admin: AdminUser, days: int = 60):
    return await analytics_service.daily_multiple(db, days)


@router.get("/referral-leaderboard")
async def referral_leaderboard(db: DbSession, admin: AdminUser):
    """Referrer leaderboard: sorted by total volume of their referred users."""
    from sqlalchemy import func, text
    from app.db.models import User, SessionSlot

    # Aggregate referred users' stats grouped by referrer
    referrers_q = await db.execute(
        select(
            User.referrer_user_id,
            func.count(User.id).label("referral_count"),
            func.sum(User.trading_volume).label("referred_volume"),
            func.sum(User.referral_reward_usdc).label("referrer_earnings"),
        )
        .where(User.referrer_user_id.isnot(None))
        .group_by(User.referrer_user_id)
        .order_by(func.sum(User.trading_volume).desc())
    )
    referrer_rows = referrers_q.all()

    referrer_ids = [r.referrer_user_id for r in referrer_rows]
    if not referrer_ids:
        return []

    # Fetch referrer display info
    referrer_users_q = await db.execute(
        select(User.id, User.display_name, User.privy_user_id)
        .where(User.id.in_(referrer_ids))
    )
    referrer_map = {u.id: u for u in referrer_users_q.all()}

    # Fetch referred users with their stats
    referred_q = await db.execute(
        select(
            User.id,
            User.display_name,
            User.privy_user_id,
            User.referrer_user_id,
            User.trading_volume,
            User.referral_reward_usdc,
        )
        .where(User.referrer_user_id.in_(referrer_ids))
        .order_by(User.trading_volume.desc())
    )
    referred_users = referred_q.all()

    # Build referred users per referrer — referral_reward_usdc on referred user
    # represents fees paid by that user that were credited to referrer
    from collections import defaultdict
    referred_by_referrer: dict[str, list] = defaultdict(list)
    for u in referred_users:
        referred_by_referrer[u.referrer_user_id].append({
            "user_id": u.id,
            "display_name": u.display_name or u.privy_user_id or u.id[:8],
            "volume": round(u.trading_volume or 0.0, 2),
            "referrer_earned": round(u.referral_reward_usdc or 0.0, 2),
        })

    result = []
    for r in referrer_rows:
        ru = referrer_map.get(r.referrer_user_id)
        result.append({
            "referrer_id": r.referrer_user_id,
            "display_name": (ru.display_name if ru else None) or (ru.privy_user_id if ru else None) or r.referrer_user_id[:8],
            "referral_count": r.referral_count or 0,
            "referred_volume": round(r.referred_volume or 0.0, 2),
            "referrer_earnings": round(r.referrer_earnings or 0.0, 2),
            "referrals": referred_by_referrer.get(r.referrer_user_id, []),
        })
    return result
