"""
session_registry.py — In-memory registry of all active session engines.

Thread-safe via asyncio.Lock.
Shared singleton for the process — NOT per-user.
"""
from __future__ import annotations
import asyncio
from typing import Optional

from app.runtime.session_engine import SessionEngine


class SessionRegistry:
    def __init__(self):
        self._engines: dict[str, SessionEngine] = {}  # session_id → SessionEngine
        self._lock = asyncio.Lock()

    async def add(self, session_id: str, engine: SessionEngine):
        async with self._lock:
            self._engines[session_id] = engine

    async def remove(self, session_id: str):
        async with self._lock:
            self._engines.pop(session_id, None)

    async def get(self, session_id: str) -> Optional[SessionEngine]:
        async with self._lock:
            return self._engines.get(session_id)

    async def get_user_session(self, user_id: str) -> Optional[SessionEngine]:
        """Return the active session engine for a user (max 1 in V1)."""
        async with self._lock:
            for engine in self._engines.values():
                if engine.user_id == user_id:
                    return engine
        return None

    async def all(self) -> list[SessionEngine]:
        async with self._lock:
            return list(self._engines.values())

    async def count(self) -> int:
        async with self._lock:
            return len(self._engines)


# Shared process-level singleton
session_registry = SessionRegistry()
