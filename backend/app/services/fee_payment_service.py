"""
fee_payment_service.py - Transfer platform fees from user fee wallet.
"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.services import funding_service
from app.services.privy_tx_service import transfer_usdc, PrivyTxError

log = logging.getLogger("uvicorn.error")


class FeePaymentError(Exception):
    pass


async def pay_fee(
    *,
    privy_wallet_id: str,
    wallet_address: str,
    amount_usdc: float,
    referrer_wallet_address: str = "",
) -> dict:
    if amount_usdc <= 0:
        raise FeePaymentError("Fee amount must be > 0")
    if not settings.PLATFORM_FEE_WALLET:
        raise FeePaymentError("Platform fee wallet not configured")

    referral_amount_usdc = round(amount_usdc * 0.10, 6) if referrer_wallet_address else 0.0
    platform_amount_usdc = round(amount_usdc - referral_amount_usdc, 6)

    usdc_balance, eth_balance = await funding_service.get_balances(wallet_address)
    usdc_balance = usdc_balance or 0.0
    eth_balance = eth_balance or 0.0
    # Must have enough to pay the fee AND keep STOP floor ($1) after
    if usdc_balance < amount_usdc + settings.FEE_WALLET_USDC_STOP:
        raise FeePaymentError("Insufficient USDC balance for fee payment")
    if eth_balance < settings.FEE_WALLET_ETH_MIN:
        raise FeePaymentError("Insufficient ETH for gas")

    try:
        platform_tx_hash = ""
        if platform_amount_usdc > 0:
            platform_tx_hash = await transfer_usdc(
                privy_wallet_id=privy_wallet_id,
                wallet_address=wallet_address,
                to_address=settings.PLATFORM_FEE_WALLET,
                amount_usdc=platform_amount_usdc,
            )
    except PrivyTxError as e:
        raise FeePaymentError(str(e)) from e

    referral_tx_hash = ""
    referral_paid_usdc = 0.0
    if referral_amount_usdc > 0:
        try:
            referral_tx_hash = await transfer_usdc(
                privy_wallet_id=privy_wallet_id,
                wallet_address=wallet_address,
                to_address=referrer_wallet_address,
                amount_usdc=referral_amount_usdc,
            )
            referral_paid_usdc = referral_amount_usdc
        except PrivyTxError as e:
            log.warning("Referral payout failed for %s -> %s: %s", wallet_address, referrer_wallet_address, e)

    return {
        "platform_tx_hash": platform_tx_hash,
        "referral_tx_hash": referral_tx_hash,
        "referral_paid_usdc": referral_paid_usdc,
        "platform_paid_usdc": platform_amount_usdc,
    }
