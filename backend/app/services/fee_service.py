"""
fee_service.py — Platform fee logic.

Fees are charged:
  - on executed fills only
  - based on executed notional (USDC)
  - proportionally for partial fills
  - from the fee_reserve, not from engaged trading capital
  - never on placement, never on session creation

fee_side: none | buy | sell | both   (on Strategy)
fee_bps:  per-user fee stored as bps x10 (on User.effective_fee_bps)

In V1: fees are tracked in-memory and DB.
Transfer mechanism: pending backend implementation
(Polymarket does not currently support embedded integrator fees in CLOB orders.
 Platform-side accounting is the safe supported approach for V1.)
"""
from __future__ import annotations
from sqlalchemy import update

from app.db.models import Strategy, User
from app.services.fee_payment_service import pay_fee, FeePaymentError


def compute_fill_fee(
    fill_usdc: float,
    fill_side: str,    # "BUY" | "SELL"
    user_fee_bps: int, # per-user effective fee stored as bps x10
    fee_side: str,     # "none" | "buy" | "sell" | "both"  (from Strategy)
) -> float:
    """
    Compute fee for a single fill event.
    Returns USDC amount to deduct from fee reserve.
    """
    if user_fee_bps <= 0 or fee_side == "none":
        return 0.0
    if fee_side == "buy" and fill_side != "BUY":
        return 0.0
    if fee_side == "sell" and fill_side != "SELL":
        return 0.0
    # "both" or matching side
    return round(fill_usdc * user_fee_bps / 100_000, 6)


class FeeService:
    """
    Processes fee events for a session.
    Called from the session engine when fills are detected.
    user_fee_bps: per-user effective fee (from User.effective_fee_bps, cached in engine).
    fee_side: from Strategy (none | buy | sell | both).
    """

    async def process_lot_opened(
        self,
        session_engine,
        fill_usdc: float,
        strategy: Strategy,
        *,
        user_fee_bps: int,
        privy_wallet_id: str,
        wallet_address: str,
        referrer_user_id: str = "",
        referrer_wallet_address: str = "",
        db_factory=None,
    ) -> float:
        fee = compute_fill_fee(fill_usdc, "BUY", user_fee_bps, strategy.fee_side)
        if fee > 0:
            session_engine.charge_fee(fee)
            payout = await pay_fee(
                privy_wallet_id=privy_wallet_id,
                wallet_address=wallet_address,
                amount_usdc=fee,
                referrer_wallet_address=referrer_wallet_address,
            )
            await self._record_referral_reward(
                referrer_user_id=referrer_user_id,
                amount_usdc=float(payout.get("referral_paid_usdc") or 0.0),
                db_factory=db_factory,
            )
        return fee

    async def process_lot_closed(
        self,
        session_engine,
        fill_usdc: float,
        strategy: Strategy,
        *,
        user_fee_bps: int,
        privy_wallet_id: str,
        wallet_address: str,
        referrer_user_id: str = "",
        referrer_wallet_address: str = "",
        db_factory=None,
    ) -> float:
        fee = compute_fill_fee(fill_usdc, "SELL", user_fee_bps, strategy.fee_side)
        if fee > 0:
            session_engine.charge_fee(fee)
            payout = await pay_fee(
                privy_wallet_id=privy_wallet_id,
                wallet_address=wallet_address,
                amount_usdc=fee,
                referrer_wallet_address=referrer_wallet_address,
            )
            await self._record_referral_reward(
                referrer_user_id=referrer_user_id,
                amount_usdc=float(payout.get("referral_paid_usdc") or 0.0),
                db_factory=db_factory,
            )
        return fee

    async def _record_referral_reward(self, *, referrer_user_id: str, amount_usdc: float, db_factory) -> None:
        if not referrer_user_id or amount_usdc <= 0 or not db_factory:
            return
        async with db_factory() as db:
            await db.execute(
                update(User)
                .where(User.id == referrer_user_id)
                .values(referral_reward_usdc=User.referral_reward_usdc + amount_usdc)
            )
            await db.commit()


fee_service = FeeService()
