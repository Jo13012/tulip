"""
user/analytics.py - User analytics endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.services.analytics_service import analytics_service

router = APIRouter(prefix="/analytics", tags=["user:analytics"])


@router.get("/daily-volume")
async def daily_volume(user: CurrentUser, db: DbSession, days: int = 60):
    return await analytics_service.daily_volume(db, days)


@router.get("/daily-cpm")
async def daily_cpm(user: CurrentUser, db: DbSession, days: int = 60):
    return await analytics_service.daily_cpm(db, days)


@router.get("/daily-multiple")
async def daily_multiple(user: CurrentUser, db: DbSession, days: int = 60):
    return await analytics_service.daily_multiple(db, days)
