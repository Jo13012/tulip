"""
execution_client.py - Rust-backed Polymarket execution client (Privy Server Wallets).
"""
from __future__ import annotations

import httpx
import time
from typing import Optional

from app.core.config import settings


class ExecutionClientError(Exception):
    pass


class ExecutionClient:
    USDC_CACHE_TTL = 8.0
    DEFAULT_CHAIN_ID = 137
    DEFAULT_SIGNATURE_TYPE = 2

    def __init__(
        self,
        privy_wallet_id: str,
        wallet_address: str,
        chain_id: int = DEFAULT_CHAIN_ID,
        proxy_address: str = "",
        signature_type: int = DEFAULT_SIGNATURE_TYPE,
    ):
        self._privy_wallet_id = privy_wallet_id
        self._wallet_address = wallet_address
        self._chain_id = chain_id
        self._proxy_address = proxy_address
        self._signature_type = signature_type
        self._client_id: Optional[str] = None
        self._http = httpx.AsyncClient(timeout=30.0, trust_env=False)
        self._initialized = False
        self._usdc_cache: float = 0.0
        self._usdc_cache_ts: float = 0.0

    async def initialize(self):
        if self._initialized:
            return

        try:
            resp = await self._http.post(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/client/open",
                json={
                    "privy_wallet_id": self._privy_wallet_id,
                    "wallet_address": self._wallet_address,
                    "chain_id": self._chain_id,
                    "proxy_address": self._proxy_address or None,
                    "signature_type": self._signature_type,
                    "builder_code": settings.POLYMARKET_BUILDER_CODE or None,
                },
            )
            if not resp.is_success:
                body = resp.text
                raise ExecutionClientError(f"Rust execution init failed: HTTP {resp.status_code} — {body}")
            payload = resp.json()
            self._client_id = payload["client_id"]
            self._initialized = True
        except ExecutionClientError:
            raise
        except Exception as e:
            raise ExecutionClientError(f"Rust execution init failed: {e}")

    async def close(self):
        if not self._client_id:
            await self._http.aclose()
            return
        try:
            await self._http.post(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/client/close",
                json={"client_id": self._client_id},
            )
        finally:
            await self._http.aclose()

    async def ensure_sell_allowance(self, token_id: str, force: bool = False):
        await self._ensure_ready()
        try:
            resp = await self._http.post(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/client/ensure-sell-allowance",
                json={
                    "client_id": self._client_id,
                    "token_id": token_id,
                    "force": force,
                },
            )
            resp.raise_for_status()
        except Exception as e:
            raise ExecutionClientError(f"CTF allowance failed for {token_id[:16]}...: {e}")

    async def get_cached_usdc(self) -> float:
        now = time.time()
        if now - self._usdc_cache_ts < self.USDC_CACHE_TTL:
            return self._usdc_cache
        return await self._fetch_usdc()

    async def _fetch_usdc(self) -> float:
        await self._ensure_ready()
        try:
            resp = await self._http.post(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/client/usdc-balance",
                json={"client_id": self._client_id},
            )
            resp.raise_for_status()
            val = float(resp.json().get("balance", 0.0))
            self._usdc_cache = val
            self._usdc_cache_ts = time.time()
            return val
        except Exception:
            return self._usdc_cache

    def invalidate_usdc_cache(self):
        self._usdc_cache_ts = 0.0

    async def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        tick_size: str = "0.001",
        neg_risk: bool = False,
    ) -> dict:
        await self._ensure_ready()
        try:
            resp = await self._http.post(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/client/place-limit-order",
                json={
                    "client_id": self._client_id,
                    "token_id": token_id,
                    "price": price,
                    "size": size,
                    "side": side,
                    "tick_size": tick_size,
                    "neg_risk": neg_risk,
                },
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            raise ExecutionClientError(f"place_limit_order failed: {e}")

    async def get_open_orders(self) -> dict[str, dict]:
        await self._ensure_ready()
        try:
            resp = await self._http.post(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/client/open-orders",
                json={"client_id": self._client_id},
            )
            resp.raise_for_status()
            raw = resp.json()
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    async def get_order_status(self, order_id: str) -> dict:
        await self._ensure_ready()
        try:
            resp = await self._http.post(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/client/order-status",
                json={"client_id": self._client_id, "order_id": order_id},
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def cancel_order(self, order_id: str) -> dict:
        await self._ensure_ready()
        try:
            resp = await self._http.post(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/client/cancel-order",
                json={"client_id": self._client_id, "order_id": order_id},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {}

    async def get_token_balance(self, token_id: str) -> Optional[float]:
        await self._ensure_ready()
        try:
            resp = await self._http.post(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/client/token-balance",
                json={"client_id": self._client_id, "token_id": token_id},
            )
            resp.raise_for_status()
            raw = float(resp.json().get("balance", 0.0))
            # V2 SDK returns human-readable token balance (not raw wei)
            return raw
        except Exception:
            return None

    async def get_all_positions(self) -> list[dict]:
        """Fetch all CTF positions from Polymarket data-api using proxy_address."""
        addr = self._proxy_address or self._wallet_address
        if not addr:
            return []
        try:
            resp = await self._http.get(
                f"{settings.DATA_API_HOST}/positions",
                params={"user": addr, "sizeThreshold": "0.01", "limit": "500"},
                timeout=10.0,
            )
            resp.raise_for_status()
            items = resp.json()
            if not isinstance(items, list):
                return []
            return [
                {
                    "token_id":     item.get("asset") or "",
                    "size":         float(item.get("size") or 0),
                    "avg_price":    float(item.get("avgPrice") or 0),
                    "cur_price":    float(item.get("curPrice") or 0),
                    "question":     item.get("title") or "",
                    "outcome":      item.get("outcome") or "",
                    "condition_id": item.get("conditionId") or "",
                    "redeemable":   bool(item.get("redeemable")),
                    "current_value": float(item.get("currentValue") or 0),
                    "icon":         item.get("icon") or "",
                }
                for item in items
                if float(item.get("size") or 0) >= 0.01 and item.get("asset")
            ]
        except Exception:
            return []

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def privy_wallet_id(self) -> str:
        return self._privy_wallet_id

    @property
    def wallet_address(self) -> str:
        return self._wallet_address

    @property
    def proxy_address(self) -> str:
        return self._proxy_address

    async def _ensure_ready(self):
        if not self._initialized:
            await self.initialize()
