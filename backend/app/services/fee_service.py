"""
fee_service.py — Platform fee logic with batched payment.

Fees are computed per-fill (immediate, in-memory) but paid in batch every
FEE_FLUSH_INTERVAL seconds — 1 Privy/Arbitrum tx per wallet per interval
instead of 1 tx per fill. This prevents RPC flooding with many concurrent sessions.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from sqlalchemy import update

from app.db.models import User
from app.services.fee_payment_service import pay_fee, FeePaymentError

log = logging.getLogger("uvicorn.error")

FEE_FLUSH_INTERVAL = 60.0   # seconds between batch flushes
FEE_MIN_BATCH      = 0.001  # minimum USDC to bother sending


def compute_fill_fee(
    fill_usdc: float,
    fill_side: str,    # "BUY" | "SELL"
    user_fee_bps: int, # per-user effective fee stored as bps x10
    fee_side: str,     # "none" | "buy" | "sell" | "both"  (from Strategy)
) -> float:
    if user_fee_bps <= 0 or fee_side == "none":
        return 0.0
    if fee_side == "buy" and fill_side != "BUY":
        return 0.0
    if fee_side == "sell" and fill_side != "SELL":
        return 0.0
    return round(fill_usdc * user_fee_bps / 100_000, 6)


@dataclass
class _WalletFeeQueue:
    privy_wallet_id: str
    wallet_address:  str
    referrer_user_id:       str = ""
    referrer_wallet_address: str = ""
    pending_usdc: float = 0.0
    # per slot_id accumulator for charge_fee calls (applied immediately to engine)
    # but payment is deferred
    session_engine_ref: object = None  # weakref-equivalent; set on first enqueue


class FeeService:
    """
    Per-fill: compute fee, charge engine immediately (accounting).
    Payment: batched every FEE_FLUSH_INTERVAL via a background task.
    """

    def __init__(self) -> None:
        # key: privy_wallet_id → _WalletFeeQueue
        self._queues: dict[str, _WalletFeeQueue] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._db_factory = None

    def start(self, db_factory) -> None:
        self._db_factory = db_factory
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._flush_loop(), name="fee_flush")
            log.info("[fee_service] batch flush started (interval=%ds)", FEE_FLUSH_INTERVAL)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final flush on shutdown
        await self._flush_all()

    # ── Called per fill from session engine ───────────────────────────────────

    async def process_lot_opened(
        self,
        session_engine,
        fill_usdc: float,
        strategy,
        *,
        slot_id: str = "",
        user_fee_bps: int,
        privy_wallet_id: str,
        wallet_address: str,
        referrer_user_id: str = "",
        referrer_wallet_address: str = "",
        db_factory=None,
    ) -> float:
        fee = compute_fill_fee(fill_usdc, "BUY", user_fee_bps, strategy.fee_side)
        if fee > 0:
            # Charge accounting immediately
            session_engine.charge_fee(fee, slot_id=slot_id or None)
            await self._enqueue(privy_wallet_id, wallet_address, fee, referrer_user_id, referrer_wallet_address)
        return fee

    async def process_lot_closed(
        self,
        session_engine,
        fill_usdc: float,
        strategy,
        *,
        slot_id: str = "",
        user_fee_bps: int,
        privy_wallet_id: str,
        wallet_address: str,
        referrer_user_id: str = "",
        referrer_wallet_address: str = "",
        db_factory=None,
    ) -> float:
        fee = compute_fill_fee(fill_usdc, "SELL", user_fee_bps, strategy.fee_side)
        if fee > 0:
            session_engine.charge_fee(fee, slot_id=slot_id or None)
            await self._enqueue(privy_wallet_id, wallet_address, fee, referrer_user_id, referrer_wallet_address)
        return fee

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _enqueue(
        self,
        privy_wallet_id: str,
        wallet_address: str,
        amount: float,
        referrer_user_id: str,
        referrer_wallet_address: str,
    ) -> None:
        async with self._lock:
            if privy_wallet_id not in self._queues:
                self._queues[privy_wallet_id] = _WalletFeeQueue(
                    privy_wallet_id=privy_wallet_id,
                    wallet_address=wallet_address,
                    referrer_user_id=referrer_user_id,
                    referrer_wallet_address=referrer_wallet_address,
                )
            self._queues[privy_wallet_id].pending_usdc = round(
                self._queues[privy_wallet_id].pending_usdc + amount, 6
            )

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FEE_FLUSH_INTERVAL)
            await self._flush_all()

    async def _flush_all(self) -> None:
        async with self._lock:
            to_flush = {k: v for k, v in self._queues.items() if v.pending_usdc >= FEE_MIN_BATCH}
            for k in to_flush:
                self._queues[k].pending_usdc = 0.0

        for privy_wallet_id, q in to_flush.items():
            try:
                payout = await pay_fee(
                    privy_wallet_id=privy_wallet_id,
                    wallet_address=q.wallet_address,
                    amount_usdc=q.pending_usdc,
                    referrer_wallet_address=q.referrer_wallet_address,
                )
                log.info(
                    "[fee_flush] paid %.4f USDC from %s (platform_tx=%s)",
                    q.pending_usdc, q.wallet_address[:12],
                    payout.get("platform_tx_hash", "")[:16],
                )
                referral_paid = float(payout.get("referral_paid_usdc") or 0.0)
                if referral_paid > 0 and q.referrer_user_id:
                    await self._record_referral_reward(q.referrer_user_id, referral_paid)
            except FeePaymentError as e:
                log.warning("[fee_flush] payment failed for %s (%.4f USDC): %s", q.wallet_address[:12], q.pending_usdc, e)
                # Re-enqueue so it retries next interval
                async with self._lock:
                    if privy_wallet_id in self._queues:
                        self._queues[privy_wallet_id].pending_usdc = round(
                            self._queues[privy_wallet_id].pending_usdc + q.pending_usdc, 6
                        )
                    else:
                        q.pending_usdc = q.pending_usdc  # restore
                        self._queues[privy_wallet_id] = q
            except Exception as e:
                log.error("[fee_flush] unexpected error for %s: %s", privy_wallet_id, e)

    async def _record_referral_reward(self, referrer_user_id: str, amount_usdc: float) -> None:
        if not self._db_factory:
            return
        try:
            async with self._db_factory() as db:
                await db.execute(
                    update(User)
                    .where(User.id == referrer_user_id)
                    .values(referral_reward_usdc=User.referral_reward_usdc + amount_usdc)
                )
                await db.commit()
        except Exception as e:
            log.warning("[fee_flush] referral record failed: %s", e)


fee_service = FeeService()
