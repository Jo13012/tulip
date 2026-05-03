"""
fee_service.py — Platform fee logic with DB-persisted queue.

Design:
  - Fill  → SQL UPSERT into fee_queue (accumulate, no Privy call)
  - Flush → every FEE_POLL_INTERVAL seconds, pick rows where:
              pending_usdc >= FEE_MIN_BATCH
              AND last_fill_at <= NOW() - FEE_DEBOUNCE_S   (wallet idle ≥ debounce)
              AND locked = FALSE
            SET locked = TRUE, pay_fee(), DELETE row
  - Crash → locked rows unlocked on startup (see bootstrap.ensure_fee_queue_table)
  - 30 users → sequential flushes, each = 1 Privy tx, no RPC flood
"""
from __future__ import annotations

import asyncio
import logging
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fee_payment_service import pay_fee, FeePaymentError

log = logging.getLogger("uvicorn.error")

FEE_POLL_INTERVAL = 10.0   # seconds between flush sweeps
FEE_DEBOUNCE_S    = 10.0   # min seconds idle after last fill before flushing
FEE_MIN_BATCH     = 0.001  # minimum USDC to bother paying


def compute_fill_fee(
    fill_usdc: float,
    fill_side: str,    # "BUY" | "SELL"
    user_fee_bps: int,
    fee_side: str,     # "none" | "buy" | "sell" | "both"
) -> float:
    if user_fee_bps <= 0 or fee_side == "none":
        return 0.0
    if fee_side == "buy"  and fill_side != "BUY":
        return 0.0
    if fee_side == "sell" and fill_side != "SELL":
        return 0.0
    return round(fill_usdc * user_fee_bps / 100_000, 6)


class FeeService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._db_factory = None

    def start(self, db_factory) -> None:
        self._db_factory = db_factory
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._flush_loop(), name="fee_flush")
            log.info("[fee_service] started (poll=%ds debounce=%ds)", FEE_POLL_INTERVAL, FEE_DEBOUNCE_S)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final flush — pay everything pending (no debounce on shutdown)
        await self._flush_all(ignore_debounce=True)

    # ── Per-fill entry points ─────────────────────────────────────────────────

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
        """Upsert into fee_queue — accumulate pending_usdc, bump last_fill_at."""
        if not self._db_factory:
            return
        async with self._db_factory() as db:
            await db.execute(text("""
                INSERT INTO fee_queue
                    (privy_wallet_id, wallet_address, referrer_user_id,
                     referrer_wallet_address, pending_usdc, last_fill_at, locked)
                VALUES
                    (:pwid, :wa, :ruid, :rwa, :amt, NOW(), FALSE)
                ON CONFLICT (privy_wallet_id) DO UPDATE
                    SET pending_usdc  = fee_queue.pending_usdc + EXCLUDED.pending_usdc,
                        last_fill_at  = NOW(),
                        wallet_address = EXCLUDED.wallet_address,
                        referrer_user_id = EXCLUDED.referrer_user_id,
                        referrer_wallet_address = EXCLUDED.referrer_wallet_address
            """), {
                "pwid": privy_wallet_id,
                "wa":   wallet_address,
                "ruid": referrer_user_id or "",
                "rwa":  referrer_wallet_address or "",
                "amt":  amount,
            })
            await db.commit()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(FEE_POLL_INTERVAL)
            try:
                await self._flush_all(ignore_debounce=False)
            except Exception as e:
                log.error("[fee_flush] loop error: %s", e)

    async def _flush_all(self, *, ignore_debounce: bool = False) -> None:
        if not self._db_factory:
            return

        async with self._db_factory() as db:
            # Select and lock eligible rows atomically
            debounce_clause = "" if ignore_debounce else (
                f"AND last_fill_at <= NOW() - INTERVAL '{FEE_DEBOUNCE_S} seconds'"
            )
            result = await db.execute(text(f"""
                UPDATE fee_queue
                SET locked = TRUE
                WHERE locked = FALSE
                  AND pending_usdc >= :min_batch
                  {debounce_clause}
                RETURNING
                    privy_wallet_id, wallet_address,
                    referrer_user_id, referrer_wallet_address,
                    pending_usdc
            """), {"min_batch": FEE_MIN_BATCH})
            rows = result.fetchall()
            await db.commit()

        if not rows:
            return

        for row in rows:
            privy_wallet_id        = row.privy_wallet_id
            wallet_address         = row.wallet_address
            referrer_user_id       = row.referrer_user_id
            referrer_wallet_address = row.referrer_wallet_address
            amount                 = float(row.pending_usdc)

            try:
                payout = await pay_fee(
                    privy_wallet_id=privy_wallet_id,
                    wallet_address=wallet_address,
                    amount_usdc=amount,
                    referrer_wallet_address=referrer_wallet_address,
                )
                log.info(
                    "[fee_flush] paid %.4f USDC from %s (tx=%s)",
                    amount, wallet_address[:12],
                    payout.get("platform_tx_hash", "")[:16],
                )
                # Delete (or keep remainder if new fills arrived during flush)
                async with self._db_factory() as db:
                    result2 = await db.execute(text("""
                        UPDATE fee_queue
                        SET locked = FALSE,
                            pending_usdc = pending_usdc - :paid
                        WHERE privy_wallet_id = :pwid AND locked = TRUE
                        RETURNING pending_usdc
                    """), {"pwid": privy_wallet_id, "paid": amount})
                    row2 = result2.fetchone()
                    if row2 and float(row2.pending_usdc) <= 0:
                        await db.execute(text(
                            "DELETE FROM fee_queue WHERE privy_wallet_id = :pwid AND pending_usdc <= 0"
                        ), {"pwid": privy_wallet_id})
                    await db.commit()

                referral_paid = float(payout.get("referral_paid_usdc") or 0.0)
                if referral_paid > 0 and referrer_user_id:
                    await self._record_referral_reward(referrer_user_id, referral_paid)

            except FeePaymentError as e:
                log.warning("[fee_flush] payment failed for %s (%.4f USDC): %s", wallet_address[:12], amount, e)
                # Unlock and restore amount — will retry next poll
                async with self._db_factory() as db:
                    await db.execute(text("""
                        UPDATE fee_queue
                        SET locked = FALSE,
                            pending_usdc = pending_usdc + :amt
                        WHERE privy_wallet_id = :pwid
                    """), {"pwid": privy_wallet_id, "amt": amount})
                    await db.commit()

            except Exception as e:
                log.error("[fee_flush] unexpected error for %s: %s", privy_wallet_id, e)
                # Unlock so next poll can retry
                async with self._db_factory() as db:
                    await db.execute(text(
                        "UPDATE fee_queue SET locked = FALSE WHERE privy_wallet_id = :pwid"
                    ), {"pwid": privy_wallet_id})
                    await db.commit()

    async def _record_referral_reward(self, referrer_user_id: str, amount_usdc: float) -> None:
        if not self._db_factory:
            return
        try:
            from app.db.models import User
            from sqlalchemy import update as sa_update
            async with self._db_factory() as db:
                await db.execute(
                    sa_update(User)
                    .where(User.id == referrer_user_id)
                    .values(referral_reward_usdc=User.referral_reward_usdc + amount_usdc)
                )
                await db.commit()
        except Exception as e:
            log.warning("[fee_flush] referral record failed: %s", e)


fee_service = FeeService()
