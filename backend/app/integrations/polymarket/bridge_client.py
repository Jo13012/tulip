"""
bridge_client.py - Thin async client for Polymarket Bridge API.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.core.config import settings


class BridgeClientError(Exception):
    pass


class BridgeClient:
    def __init__(self):
        self._http: Optional[httpx.AsyncClient] = None

    async def init(self):
        if not self._http:
            self._http = httpx.AsyncClient(timeout=15.0, trust_env=False)

    async def close(self):
        if self._http:
            await self._http.aclose()
            self._http = None

    async def _request(self, method: str, path: str, *, json: dict | None = None) -> Any:
        if not self._http:
            await self.init()

        assert self._http is not None

        try:
            resp = await self._http.request(
                method,
                f"{settings.POLYMARKET_BRIDGE_URL}{path}",
                json=json,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            detail = e.response.json().get("detail", e.response.text)
            raise BridgeClientError(detail)
        except Exception as e:
            raise BridgeClientError(f"Bridge API unavailable: {e}")

    async def get_supported_assets(self) -> dict:
        return await self._request("GET", "/supported-assets")

    async def create_deposit_addresses(self, polymarket_address: str) -> dict:
        return await self._request(
            "POST",
            "/deposit",
            json={"address": polymarket_address},
        )

    async def get_transaction_status(self, deposit_address: str) -> dict:
        return await self._request("GET", f"/status/{deposit_address}")


bridge_client = BridgeClient()
