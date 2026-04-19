"""
profile_service.py — Wallet stats from Polymarket leaderboard API.

Cache: 60s TTL per user (in-memory).
Fallback: returns trading_volume / bot_pnl from DB if API fails — never null.
Optimized for 50+ users: shared httpx client, per-user TTL, double-checked locking.
"""
from __future__ import annotations
import time
import asyncio
import httpx
from typing import Optional

_LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
_TTL = 60.0  # seconds


class ProfileService:
    def __init__(self):
        # cache: user_id -> (timestamp, {"wallet_volume": float, "wallet_pnl": float})
        self._cache: dict[str, tuple[float, dict]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._http: Optional[httpx.AsyncClient] = None

    def _get_lock(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=8.0, trust_env=False)
        return self._http

    async def get_wallet_stats(
        self,
        user_id: str,
        proxy_address: str,
        *,
        fallback_volume: float = 0.0,
        fallback_pnl: float = 0.0,
    ) -> dict:
        """
        Returns {"wallet_volume": float, "wallet_pnl": float}.
        Falls back to (fallback_volume, fallback_pnl) on error.
        Never returns null values.
        """
        now = time.monotonic()
        cached_ts, cached_data = self._cache.get(user_id, (0.0, None))
        if cached_data is not None and now - cached_ts < _TTL:
            return cached_data

        lock = self._get_lock(user_id)
        async with lock:
            # re-check after acquiring lock
            cached_ts, cached_data = self._cache.get(user_id, (0.0, None))
            if cached_data is not None and now - cached_ts < _TTL:
                return cached_data

            try:
                params = {
                    "category": "OVERALL",
                    "timePeriod": "ALL",
                    "orderBy": "PNL",
                    "limit": "25",
                    "user": proxy_address.lower(),
                }
                resp = await self._client().get(_LEADERBOARD_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

                entries = data if isinstance(data, list) else data.get("data", [])
                result = {"wallet_volume": fallback_volume, "wallet_pnl": fallback_pnl}
                addr_lower = proxy_address.lower()
                for entry in entries:
                    if entry.get("proxyAddress", "").lower() == addr_lower or \
                       entry.get("address", "").lower() == addr_lower:
                        vol = float(entry.get("vol") or entry.get("volume") or 0)
                        pnl = float(entry.get("pnl") or 0)
                        result = {
                            "wallet_volume": round(max(vol, fallback_volume), 4),
                            "wallet_pnl":   round(pnl, 4),
                        }
                        break

                self._cache[user_id] = (time.monotonic(), result)
                return result

            except Exception:
                # Stale cache is fine; fallback to DB values
                if cached_data is not None:
                    return cached_data
                return {"wallet_volume": fallback_volume, "wallet_pnl": fallback_pnl}

    async def close(self):
        if self._http and not self._http.is_closed:
            await self._http.aclose()


profile_service = ProfileService()
