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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.bootstrap import (
    ensure_user_profile_columns,
    ensure_wallet_funding_columns,
    ensure_wallet_v2_migration_column,
    ensure_global_strategy,
    ensure_funding_transactions_table,
    ensure_strategy_slot_version_columns,
    reset_stale_funding_states,
)

_perf_log = logging.getLogger("perf")
from app.runtime.market_data_service import market_data_service
from app.integrations.polymarket.market_resolver import market_resolver
from app.integrations.polymarket.bridge_client import bridge_client
from app.services.recovery_service import recovery_service
from app.services.privy_wallet_service import validate_authorization_config
from app.services.balance_poller import balance_poller

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


async def _auto_migrate_v2_wallets() -> None:
    """
    Background task: submit V2 token approvals for all onboarded wallets
    that haven't been migrated yet. Runs once at startup, parallel per wallet.
    """
    import asyncio as _asyncio
    from sqlalchemy import select as _select
    from app.db.session import async_session_factory as _sf
    from app.db.models import Wallet as _Wallet
    from app.services.polymarket_onboarding_service import run_v2_approvals

    _log = logging.getLogger("uvicorn.error")

    await _asyncio.sleep(5)  # let the app finish starting up first

    try:
        async with _sf() as db:
            q = await db.execute(
                _select(_Wallet).where(
                    _Wallet.onboarding_complete == True,
                    _Wallet.v2_migration_done == False,
                    _Wallet.proxy_address.isnot(None),
                    _Wallet.privy_wallet_id.isnot(None),
                )
            )
            wallets = q.scalars().all()

        if not wallets:
            _log.info("[v2_migration] all wallets already migrated")
            return

        _log.info("[v2_migration] migrating %d wallet(s)...", len(wallets))

        async def _migrate_one(w) -> None:
            try:
                await run_v2_approvals(w.privy_wallet_id, w.wallet_address, w.proxy_address)
                async with _sf() as db:
                    wallet = await db.get(_Wallet, w.id)
                    if wallet:
                        wallet.v2_migration_done = True
                        await db.commit()
                _log.info("[v2_migration] done wallet=%s proxy=%s", w.wallet_address[:12], w.proxy_address[:12])
            except Exception as e:
                _log.warning("[v2_migration] failed wallet=%s: %s", w.wallet_address[:12] if w.wallet_address else "?", e)

        await _asyncio.gather(*[_migrate_one(w) for w in wallets])
        _log.info("[v2_migration] completed")

    except Exception as e:
        _log.warning("[v2_migration] startup migration error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — DB migrations (non-fatal: skip if DB is unreachable at boot)
    _boot_log = logging.getLogger("uvicorn.error")
    try:
        await ensure_user_profile_columns()
        await ensure_wallet_funding_columns()
        await ensure_wallet_v2_migration_column()
        await ensure_funding_transactions_table()
        await ensure_strategy_slot_version_columns()
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

    # Auto-migrate existing wallets to V2 approvals (background, non-blocking)
    asyncio.create_task(_auto_migrate_v2_wallets())

    # Start live wallet balance poller (30s TTL cache for admin/users)
    balance_poller.start()

    yield

    # Shutdown
    await balance_poller.stop()
    await market_data_service.close()
    await market_resolver.close()
    await bridge_client.close()
    from app.services.funding_service import close_clients as _close_http
    await _close_http()


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
    _perf_log.info("%.0fms  %s %s → %d", ms, request.method, request.url.path, response.status_code)
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
