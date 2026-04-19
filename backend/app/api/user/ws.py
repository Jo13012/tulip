"""
user/ws.py — User WebSocket for live session updates.

Auth: token passed as query param `?token=<privy_or_dev_token>`.
Sends periodic session runtime summaries + log events.
Sessions continue server-side even if WS disconnects.
"""
from __future__ import annotations
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import verify_privy_token
from app.core.deps import get_current_user
from app.db.models import User
from app.db.session import async_session_factory
from app.runtime.session_registry import session_registry
from sqlalchemy import select

router = APIRouter(tags=["user:ws"])

PUSH_INTERVAL = 2.0  # seconds between runtime state pushes


@router.websocket("/ws/user/{session_id}")
async def user_session_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()

    # Auth via query param token
    token = websocket.query_params.get("token", "")
    claims = await verify_privy_token(token)
    if not claims:
        await websocket.close(code=4001)
        return

    privy_user_id = claims.get("sub") or claims.get("userId", "")
    if not privy_user_id:
        await websocket.close(code=4001)
        return

    # Resolve DB user_id from privy_user_id
    async with async_session_factory() as db:
        q = await db.execute(select(User).where(User.privy_user_id == privy_user_id))
        user = q.scalars().first()

    if not user:
        await websocket.send_json({"type": "error", "detail": "User not found"})
        await websocket.close(code=4001)
        return

    user_id = user.id
    engine  = await session_registry.get(session_id)

    if not engine or engine.user_id != user_id:
        await websocket.send_json({"type": "error", "detail": "Session not found or not yours"})
        await websocket.close(code=4004)
        return

    # Subscribe to session logs
    log_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    orig_log = engine._on_log

    async def log_relay(level: str, msg: str, slot_id: str = ""):
        try:
            log_queue.put_nowait({"level": level, "message": msg, "slot_id": slot_id})
        except asyncio.QueueFull:
            pass
        if orig_log:
            await orig_log(level, msg, slot_id)

    engine._on_log = log_relay

    try:
        while True:
            try:
                summary = engine.summary.model_dump()
                slot_stats = engine.all_slot_stats()
                await websocket.send_json({
                    "type": "summary",
                    "data": summary,
                    "slots": slot_stats,
                })
            except Exception:
                break

            logs = []
            while not log_queue.empty():
                try:
                    logs.append(log_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if logs:
                await websocket.send_json({"type": "logs", "data": logs})

            await asyncio.sleep(PUSH_INTERVAL)

    except WebSocketDisconnect:
        pass
    finally:
        engine._on_log = orig_log
