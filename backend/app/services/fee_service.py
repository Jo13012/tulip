"""
fee_service.py — Referral reward tracking (platform fee queue removed).
"""
from __future__ import annotations

import logging
from sqlalchemy import update as sa_update

log = logging.getLogger("uvicorn.error")


async def record_referral_reward(db_factory, referrer_user_id: str, amount_usdc: float) -> None:
    if not db_factory or not referrer_user_id or amount_usdc <= 0:
        return
    try:
        from app.db.models import User
        async with db_factory() as db:
            await db.execute(
                sa_update(User)
                .where(User.id == referrer_user_id)
                .values(referral_reward_usdc=User.referral_reward_usdc + amount_usdc)
            )
            await db.commit()
    except Exception as e:
        log.warning("[referral] record failed: %s", e)
