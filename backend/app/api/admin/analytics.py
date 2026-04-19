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

    # Add live engine data (running sessions not yet in SessionSlotMarketHistory)
    from app.runtime.session_registry import session_registry
    engines = await session_registry.all()
    for engine in engines:
        sm = engine.summary
        summary["total_volume"] = round(summary["total_volume"] + (sm.total_volume or 0), 2)
        summary["total_pnl"]    = round(summary["total_pnl"]    + (sm.realized_pnl or 0), 2)
        summary["total_fees"]   = round(summary["total_fees"]   + (sm.paid_fees or 0), 2)

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
