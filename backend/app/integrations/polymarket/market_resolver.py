"""
market_resolver.py - Rust-backed Polymarket market resolution.
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.core.config import settings


class MarketResolverError(Exception):
    pass


class MarketResolver:
    """
    Resolves Polymarket URLs through the local Rust service.
    """

    def __init__(self):
        self._http: Optional[httpx.AsyncClient] = None

    async def init(self):
        if not self._http:
            self._http = httpx.AsyncClient(timeout=15.0, trust_env=False)

    async def close(self):
        if self._http:
            await self._http.aclose()
            self._http = None

    async def resolve_market(self, polymarket_url: str) -> dict:
        if not self._http:
            await self.init()

        try:
            resp = await self._http.get(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/resolve-market",
                params={"url": polymarket_url},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            detail = e.response.json().get("detail", e.response.text)
            raise MarketResolverError(detail)
        except Exception as e:
            raise MarketResolverError(f"Rust resolver unavailable: {e}")

    async def resolve_event(self, slug: str) -> dict:
        """Resolve a Polymarket event slug → list of sub-markets."""
        if not self._http:
            await self.init()
        try:
            resp = await self._http.get(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/resolve-event",
                params={"slug": slug},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            detail = e.response.json().get("detail", e.response.text)
            raise MarketResolverError(detail)
        except Exception as e:
            raise MarketResolverError(f"Rust resolver unavailable: {e}")

    async def get_book(self, token_id: str) -> dict:
        """Fetch live orderbook from Rust service."""
        if not self._http:
            await self.init()
        try:
            resp = await self._http.get(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/book/{token_id}",
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            detail = e.response.json().get("detail", e.response.text)
            raise MarketResolverError(detail)
        except Exception as e:
            raise MarketResolverError(f"Rust resolver unavailable: {e}")

    async def resolve_and_validate(self, polymarket_url: str) -> dict:
        info = await self.resolve_market(polymarket_url)
        if info.get("closed"):
            raise MarketResolverError("Market is closed")
        if not info.get("active", True):
            raise MarketResolverError("Market is inactive")
        return info


market_resolver = MarketResolver()
