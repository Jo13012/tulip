"""
balance_poller.py — Single source of truth for all wallet balances.

Design:
  - Polls ALL primary wallets every POLL_INTERVAL seconds (1 RPC batch + parallel Rust).
  - First poll fires IMMEDIATELY at startup (no initial sleep).
  - get()            → instant cache read, always O(1), never blocks.
  - get_arb()        → (usdc, eth) from cache for session_engine / launch guards.
  - get_trading()    → trading balance from cache.
  - refresh_user()   → force single-user refresh (after bridge).
  - Cache TTL POLL_INTERVAL — stale reads are acceptable between polls.
  - Timeout on RPC batch → keeps previous cached values (no reset to None).
  - 50 users: 1 JSON-RPC batch for all ARB balances + 50 parallel Rust calls.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("uvicorn.error")

POLL_INTERVAL       = 10.0   # seconds between full refreshes (was 30)
_ARB_BATCH_TIMEOUT  = 8.0    # JSON-RPC batch timeout (all wallets in 1 call)
_TRADING_TIMEOUT    = 4.0    # per-wallet Rust call timeout


@dataclass
class WalletBalances:
    wallet_usdc:     float | None = None
    wallet_eth:      float | None = None
    trading_balance: float | None = None
    fetched_at:      float = field(default_factory=time.monotonic)


class BalancePoller:
    def __init__(self) -> None:
        self._cache: dict[str, WalletBalances] = {}  # user_id → WalletBalances
        self._task:  asyncio.Task | None = None
        self._ready = asyncio.Event()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="balance_poller")
            log.info("[balance_poller] started (poll=%.0fs)", POLL_INTERVAL)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Public API — all O(1), never block ───────────────────────────────────

    def get(self, user_id: str) -> WalletBalances:
        return self._cache.get(user_id, WalletBalances())

    def get_arb(self, user_id: str) -> tuple[float | None, float | None]:
        """(usdc, eth) from cache. Used by session_engine + launch guards."""
        bal = self._cache.get(user_id)
        if bal is None:
            return None, None
        return bal.wallet_usdc, bal.wallet_eth

    def get_trading(self, user_id: str) -> float | None:
        """Polymarket trading balance from cache."""
        bal = self._cache.get(user_id)
        return bal.trading_balance if bal else None

    async def refresh_user(self, user_id: str, wallet_address: str, proxy_address: str) -> None:
        """Force-refresh one user (after bridge, or on cold cache miss)."""
        from app.services.funding_service import get_balances_batch
        prev = self._cache.get(user_id, WalletBalances())
        try:
            arb = await asyncio.wait_for(
                get_balances_batch([wallet_address]),
                timeout=_ARB_BATCH_TIMEOUT,
            ) if wallet_address else {}
            usdc, eth = arb.get(wallet_address, (prev.wallet_usdc, prev.wallet_eth))
        except Exception:
            usdc, eth = prev.wallet_usdc, prev.wallet_eth

        try:
            trading = await asyncio.wait_for(
                self._fetch_trading(proxy_address),
                timeout=_TRADING_TIMEOUT,
            ) if proxy_address else prev.trading_balance
        except Exception:
            trading = prev.trading_balance

        self._cache[user_id] = WalletBalances(wallet_usdc=usdc, wallet_eth=eth, trading_balance=trading)

    async def wait_ready(self, timeout: float = 6.0) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(self._ready.wait()), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        # First poll IMMEDIATELY — no sleep at start
        while True:
            try:
                await self._poll_all()
                if not self._ready.is_set():
                    self._ready.set()
            except Exception as e:
                msg = str(e)
                if not any(k in msg for k in ("getaddrinfo", "ConnectError", "Name or service not known", "nodename nor servname", "Connection refused")):
                    log.warning("[balance_poller] poll error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_all(self) -> None:
        from app.db.session import async_session_factory
        from app.db.models import User, Wallet
        from sqlalchemy import select
        from app.services.funding_service import get_balances_batch

        async with async_session_factory() as db:
            rows = await db.execute(
                select(User.id, Wallet.wallet_address, Wallet.proxy_address)
                .join(Wallet, (Wallet.user_id == User.id) & (Wallet.is_primary == True))
            )
            wallet_rows = rows.all()

        if not wallet_rows:
            return

        addresses       = [wa or "" for _, wa, _ in wallet_rows]
        proxy_addresses = [pa or "" for _, _, pa in wallet_rows]

        # 1 JSON-RPC batch for all ARB wallets — keeps previous value on timeout
        try:
            arb_balances = await asyncio.wait_for(
                get_balances_batch(addresses),
                timeout=_ARB_BATCH_TIMEOUT,
            )
        except Exception:
            arb_balances = {}  # keep previous cached values below

        # All trading balances in parallel (Rust internal service, fast)
        trading_results = await asyncio.gather(
            *[self._fetch_trading_safe(pa) for pa in proxy_addresses],
            return_exceptions=True,
        )

        for i, (user_id, wa, _) in enumerate(wallet_rows):
            prev = self._cache.get(user_id)
            if wa and wa in arb_balances:
                usdc, eth = arb_balances[wa]
            else:
                # Timeout or missing — keep previous, don't reset to None
                usdc = prev.wallet_usdc if prev else None
                eth  = prev.wallet_eth  if prev else None

            tr = trading_results[i]
            if isinstance(tr, (float, int)):
                trading = float(tr)
            elif tr is None:
                trading = prev.trading_balance if prev else None
            else:
                # Exception — keep previous
                trading = prev.trading_balance if prev else None

            self._cache[user_id] = WalletBalances(wallet_usdc=usdc, wallet_eth=eth, trading_balance=trading)

    async def _fetch_trading(self, proxy_address: str) -> float | None:
        if not proxy_address:
            return None
        from app.services.funding_service import get_rust_client
        resp = await get_rust_client().get(f"/api/v1/wallet-balance/{proxy_address}")
        if resp.is_success:
            return resp.json().get("balance")
        return None

    async def _fetch_trading_safe(self, proxy_address: str) -> float | None:
        if not proxy_address:
            return None
        try:
            return await asyncio.wait_for(self._fetch_trading(proxy_address), timeout=_TRADING_TIMEOUT)
        except Exception:
            return None


balance_poller = BalancePoller()
