"""
market_data_service.py - Rust-backed orderbook service.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx

from app.core.config import settings


class MarketClosedError(Exception):
    """Raised when the orderbook returns 404 — market resolved or delisted."""
    pass


class MarketDataService:
    BOOK_CACHE_TTL = 1.0

    def __init__(self):
        self._http: Optional[httpx.AsyncClient] = None
        self._book_cache: dict[str, tuple[dict, float]] = {}
        self._cache_lock = asyncio.Lock()

    async def init(self):
        if not self._http:
            self._http = httpx.AsyncClient(timeout=15.0, trust_env=False)

    async def close(self):
        if self._http:
            await self._http.aclose()
            self._http = None

    async def get_order_book(self, token_id: str) -> dict:
        async with self._cache_lock:
            cached = self._book_cache.get(token_id)
            if cached and (time.time() - cached[1]) < self.BOOK_CACHE_TTL:
                return cached[0]

        if not self._http:
            await self.init()

        try:
            resp = await self._http.get(
                f"{settings.POLYMARKET_RUST_URL}/api/v1/book/{token_id}"
            )
            if resp.status_code == 404:
                raise MarketClosedError(f"Market closed/resolved: {token_id[:16]}…")
            resp.raise_for_status()
            book = resp.json()
        except MarketClosedError:
            raise
        except Exception:
            async with self._cache_lock:
                cached = self._book_cache.get(token_id)
            book = cached[0] if cached else {"bids": [], "asks": []}
        else:
            async with self._cache_lock:
                self._book_cache[token_id] = (book, time.time())

        return book

    async def get_midpoint(self, token_id: str) -> float:
        book = await self.get_order_book(token_id)
        try:
            bid = float(book["bids"][0]["price"]) if book.get("bids") else 0.49
            ask = float(book["asks"][0]["price"]) if book.get("asks") else 0.51
            return round((bid + ask) / 2, 4)
        except (TypeError, ValueError, KeyError, IndexError):
            return 0.50

    async def get_best_bid_ask(self, token_id: str) -> tuple[Optional[float], Optional[float]]:
        book = await self.get_order_book(token_id)
        try:
            bid = float(book["bids"][0]["price"]) if book.get("bids") else None
            ask = float(book["asks"][0]["price"]) if book.get("asks") else None
            return bid, ask
        except (TypeError, ValueError, KeyError, IndexError):
            return None, None


market_data_service = MarketDataService()
