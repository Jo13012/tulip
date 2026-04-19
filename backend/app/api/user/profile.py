"""
user/profile.py — User profile endpoint.

GET /user/profile — returns bot stats + wallet stats (Polymarket API, 60s cache).
Response always has numeric values (never null) — fallback to DB values on API failure.
"""
from __future__ import annotations
from fastapi import APIRouter
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.db.models import User, Wallet
from app.services.profile_service import profile_service

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

    trading_volume    = db_user.trading_volume or 0.0
    bot_pnl           = db_user.bot_pnl or 0.0
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
