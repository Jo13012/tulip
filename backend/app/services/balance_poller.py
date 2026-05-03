"""
balance_poller.py — Background service that keeps live wallet balances in memory.

Design:
  - Polls all primary wallets every POLL_INTERVAL seconds.
  - Fetches USDC + ETH (Arbitrum RPC) and trading balance (Rust service) in parallel.
  - Stores results in a dict keyed by user_id.
  - list_users reads from this cache instantly — zero blocking.
  - First call waits up to FIRST_LOAD_TIMEOUT if cache is cold.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("uvicorn.error")

POLL_INTERVAL       = 30.0   # seconds between full refreshes
FIRST_LOAD_TIMEOUT  = 8.0    # max wait on first call if cache is cold
_PER_WALLET_TIMEOUT = 5.0    # per-wallet fetch timeout (USDC/ETH/trading)


@dataclass
class WalletBalances:
    wallet_usdc:      float | None = None
    wallet_eth:       float | None = None
    trading_balance:  float | None = None
    fetched_at:       float = field(default_factory=time.monotonic)


class BalancePoller:
    def __init__(self) -> None:
        self._cache: dict[str, WalletBalances] = {}   # user_id → balances
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()   # set after first successful poll

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background polling loop. Call once at app startup."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="balance_poller")
            log.info("[balance_poller] started")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("[balance_poller] stopped")

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, user_id: str) -> WalletBalances:
        """Return cached balances for user_id. Returns empty WalletBalances if not yet fetched."""
        return self._cache.get(user_id, WalletBalances())

    async def wait_ready(self, timeout: float = FIRST_LOAD_TIMEOUT) -> None:
        """Block until first poll completes (or timeout). Safe to call multiple times."""
        try:
            await asyncio.wait_for(asyncio.shield(self._ready.wait()), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("[balance_poller] first load timed out after %.1fs", timeout)

    async def refresh_user(self, user_id: str, wallet_address: str, proxy_address: str) -> None:
        """Force-refresh a single user's balances (e.g. after bridge)."""
        from app.services.funding_service import get_balances_batch
        arb = await get_balances_batch([wallet_address])
        usdc, eth = arb.get(wallet_address, (None, None))
        trading = await self._fetch_trading(proxy_address)
        self._cache[user_id] = WalletBalances(wallet_usdc=usdc, wallet_eth=eth, trading_balance=trading)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
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
        from app.services.funding_service import get_balances_batch, get_rust_client

        async with async_session_factory() as db:
            rows = await db.execute(
                select(User.id, Wallet.wallet_address, Wallet.proxy_address)
                .join(Wallet, (Wallet.user_id == User.id) & (Wallet.is_primary == True))
            )
            wallet_rows = rows.all()   # [(user_id, wallet_address, proxy_address), ...]

        if not wallet_rows:
            return

        addresses = [wa or "" for _, wa, _ in wallet_rows]
        proxy_addresses = [pa or "" for _, _, pa in wallet_rows]

        # 1 JSON-RPC batch call for all USDC+ETH balances
        try:
            arb_balances = await asyncio.wait_for(
                get_balances_batch(addresses),
                timeout=_PER_WALLET_TIMEOUT,
            )
        except Exception:
            arb_balances = {}

        # Trading balances: still parallel but via Rust (fast internal service)
        trading_results = await asyncio.gather(
            *[self._fetch_trading(pa) for pa in proxy_addresses],
            return_exceptions=True,
        )

        for i, (user_id, wa, _) in enumerate(wallet_rows):
            usdc, eth = arb_balances.get(wa or "", (None, None)) if wa else (None, None)
            trading = trading_results[i] if isinstance(trading_results[i], (float, int, type(None))) else None
            self._cache[user_id] = WalletBalances(wallet_usdc=usdc, wallet_eth=eth, trading_balance=trading)


    async def _fetch_trading(self, proxy_address: str) -> float | None:
        if not proxy_address:
            return None
        try:
            from app.services.funding_service import get_rust_client
            resp = await asyncio.wait_for(
                get_rust_client().get(f"/api/v1/wallet-balance/{proxy_address}"),
                timeout=_PER_WALLET_TIMEOUT,
            )
            if resp.is_success:
                return resp.json().get("balance")
        except Exception:
            pass
        return None


balance_poller = BalancePoller()
