"""
main.py — FastAPI application entry point for multi_user_bot backend.

Port: 8010
"""
from __future__ import annotations
import asyncio
import sys
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
# Make sure our service loggers are visible
for _lg in ("nuclear_close", "redeem", "polymarket_onboarding"):
    logging.getLogger(_lg).setLevel(logging.DEBUG)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.bootstrap import (
    ensure_user_profile_columns,
    ensure_wallet_funding_columns,
    ensure_strategy_free_fee_column,
    ensure_user_free_fee_ends_at_column,
    ensure_global_strategy,
    ensure_funding_transactions_table,
    ensure_strategy_slot_version_columns,
    ensure_fee_queue_table,
    reset_stale_funding_states,
)

from app.runtime.market_data_service import market_data_service
from app.integrations.polymarket.market_resolver import market_resolver
from app.integrations.polymarket.bridge_client import bridge_client
from app.services.recovery_service import recovery_service
from app.services.privy_wallet_service import validate_authorization_config
from app.services.balance_poller import balance_poller
from app.services.fee_service import fee_service

# ── API routers ───────────────────────────────────────────────────────────────
from app.api.auth import router as auth_router
from app.api.user.sessions import router as user_sessions_router
from app.api.user.strategy import router as user_strategy_router
from app.api.user.ws import router as user_ws_router
from app.api.user.wallets import router as user_wallets_router
from app.api.user.analytics import router as user_analytics_router
from app.api.admin.strategy import router as admin_strategy_router
from app.api.admin.sessions import router as admin_sessions_router
from app.api.admin.analytics import router as admin_analytics_router
from app.api.admin.controls import router as admin_controls_router
from app.api.admin.users import router as admin_users_router
from app.api.user.profile import router as user_profile_router



@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — DB migrations (non-fatal: skip if DB is unreachable at boot)
    _boot_log = logging.getLogger("uvicorn.error")
    try:
        await ensure_user_profile_columns()
        await ensure_wallet_funding_columns()
        await ensure_strategy_free_fee_column()
        await ensure_user_free_fee_ends_at_column()
        await ensure_funding_transactions_table()
        await ensure_strategy_slot_version_columns()
        await ensure_fee_queue_table()
        await ensure_global_strategy()
        await reset_stale_funding_states()
    except Exception as _e:
        _boot_log.warning("[startup] DB bootstrap skipped (DB unreachable): %s", _e)
    auth_ok, auth_msg = validate_authorization_config()
    logging.getLogger("uvicorn.error").info(auth_msg)
    if not auth_ok:
        logging.getLogger("uvicorn.error").warning(
            "Privy authorization config invalid. Wallet onboarding may work, but bridge/send flows will fail until fixed."
        )
    await market_data_service.init()
    await market_resolver.init()
    await bridge_client.init()

    # Recover active sessions from DB
    asyncio.create_task(recovery_service.recover_all_sessions())

    # Start live wallet balance poller (30s TTL cache for admin/users)
    balance_poller.start()

    # Start fee flush (DB-persisted queue, 10s poll, 10s debounce)
    from app.db.session import async_session_factory
    fee_service.start(async_session_factory)

    yield

    # Shutdown
    await fee_service.stop()
    await balance_poller.stop()
    await market_data_service.close()
    await market_resolver.close()
    await bridge_client.close()
    from app.services.funding_service import close_clients as _close_http
    from app.services.privy_tx_service import close_privy_client
    await _close_http()
    await close_privy_client()


app = FastAPI(
    title="Polymarket Multi-User Bot API",
    version="1.0.0",
    lifespan=lifespan,
)

@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    response.headers["X-Response-Time-Ms"] = f"{ms:.0f}"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(auth_router,            prefix="/api/v1")
app.include_router(user_sessions_router,   prefix="/api/v1/user")
app.include_router(user_strategy_router,   prefix="/api/v1/user")
app.include_router(user_wallets_router,    prefix="/api/v1/user")
app.include_router(user_analytics_router,  prefix="/api/v1/user")
app.include_router(user_ws_router)
app.include_router(admin_strategy_router,  prefix="/api/v1")
app.include_router(admin_sessions_router,  prefix="/api/v1")
app.include_router(admin_analytics_router, prefix="/api/v1")
app.include_router(admin_controls_router,  prefix="/api/v1")
app.include_router(admin_users_router,     prefix="/api/v1")
app.include_router(user_profile_router,    prefix="/api/v1/user")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.HTTP_PORT, reload=True)
