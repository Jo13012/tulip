"""
user/strategy.py — Global strategy info for users (read-only).

Returns the one global strategy. Users never select a strategy.
"""
from __future__ import annotations
import httpx
from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import select

from app.core.deps import DbSession
from app.db.models import Strategy, StrategyVersion
from app.services.analytics_service import analytics_service

router = APIRouter(prefix="/strategy", tags=["user:strategy"])

_gamma_client = httpx.AsyncClient(timeout=8.0)


@router.get("/market-image")
async def proxy_market_image(conditionId: str = Query(...)):
    """Proxy CLOB API market image to avoid CORS in browser."""
    url = f"https://clob.polymarket.com/markets/{conditionId}"
    try:
        r = await _gamma_client.get(url)
        if r.status_code == 200:
            data = r.json()
            img_url = data.get("image") or data.get("icon") or ""
            return {"image": img_url}
        return {"image": ""}
    except Exception:
        return {"image": ""}


@router.get("/current")
async def get_current_strategy(db: DbSession):
    """
    Return the global strategy visible to users.
    Always returns a response (even if not published) so the UI can
    show the appropriate launch-unavailable state.
    """
    q = await db.execute(
        select(Strategy).order_by(Strategy.created_at.asc()).limit(1)
    )
    strat = q.scalars().first()
    if not strat:
        return {
            "strategy_id": None,
            "available": False,
            "unavailable_reason": "Platform not yet configured",
            "global_pause": False,
            "version_label": None,
            "version_id": None,
            "reserve_pct": 5.0,
            "estimate": None,
        }

    ver_q = await db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strat.id)
        .order_by(StrategyVersion.published_at.desc())
        .limit(1)
    )
    version = ver_q.scalars().first()

    # Determine availability
    available = strat.status == "published" and not strat.global_pause
    unavailable_reason = None
    if strat.status != "published":
        unavailable_reason = "Strategy not yet published"
    elif strat.global_pause:
        unavailable_reason = "Platform is paused"

    estimate = None
    if available and strat.id:
        estimate = await analytics_service.strategy_estimate_for_user(db, strat.id)

    return {
        "strategy_id":        strat.id,
        "available":          available,
        "unavailable_reason": unavailable_reason,
        "global_pause":       strat.global_pause,
        "reserve_pct":        strat.reserve_pct,
        "version_label":      version.version_label if version else None,
        "version_id":         version.id if version else None,
        "estimate":           estimate,
    }
