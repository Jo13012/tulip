"""
user/wallets.py - User wallet endpoints.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession
from app.db.models import Wallet, FundingTransaction
from app.integrations.polymarket.bridge_client import bridge_client, BridgeClientError
from app.services import funding_service
from app.services.privy_tx_service import transfer_usdc, PrivyTxError

router = APIRouter(prefix="/wallets", tags=["user:wallets"])
FUNDING_ENABLED = True
log = logging.getLogger("uvicorn.error")



async def _get_primary_wallet(user_id: str, db: DbSession, *, for_update: bool = False) -> Wallet | None:
    stmt = select(Wallet).where(Wallet.user_id == user_id, Wallet.is_primary == True)
    if for_update:
        stmt = stmt.with_for_update()
    q = await db.execute(stmt)
    return q.scalars().first()


def _onboarding_checks(wallet: Wallet | None) -> dict[str, dict[str, str]]:
    if not wallet:
        return {
            "connect": {"status": "ok", "reason": ""},
            "verify": {"status": "ok", "reason": ""},
            "wallet": {"status": "failed", "reason": "Wallet record was not created."},
            "prepare": {"status": "idle", "reason": ""},
            "finalize": {"status": "idle", "reason": ""},
        }

    error_detail = (wallet.onboarding_error_detail or "").strip()
    state = wallet.onboarding_state
    wallet_ready = bool(wallet.wallet_address and wallet.privy_wallet_id)
    prepare_ready = bool(wallet.proxy_address)
    finalize_ready = bool(wallet.clob_api_key and wallet.clob_api_secret and wallet.clob_api_passphrase)

    checks = {
        "connect": {"status": "ok", "reason": ""},
        "verify": {"status": "ok", "reason": ""},
        "wallet": {
            "status": "ok" if wallet_ready else "pending",
            "reason": "" if wallet_ready else "Waiting for Privy wallet creation.",
        },
        "prepare": {
            "status": "ok" if prepare_ready or state in ("approving", "finalizing", "ready") else "pending",
            "reason": "" if prepare_ready or state in ("approving", "finalizing", "ready") else "Waiting for Polymarket safe deployment.",
        },
        "finalize": {
            "status": "ok" if finalize_ready or state == "ready" else "pending",
            "reason": "" if finalize_ready or state == "ready" else "Waiting for CLOB credentials and approvals.",
        },
    }

    if state == "deploying":
        checks["wallet"]["status"] = "active"
        checks["wallet"]["reason"] = "Creating or attaching your Privy wallet."
        checks["prepare"]["reason"] = "Waiting for wallet setup to finish."
    elif state == "approving":
        checks["prepare"]["status"] = "active"
        checks["prepare"]["reason"] = "Deploying your Polymarket account and approvals."
    elif state == "finalizing":
        checks["prepare"]["status"] = "ok"
        checks["finalize"]["status"] = "active"
        checks["finalize"]["reason"] = "Saving credentials and final account data."
    elif state == "ready":
        checks["wallet"]["status"] = "ok"
        checks["prepare"]["status"] = "ok"
        checks["finalize"]["status"] = "ok"
    elif state == "failed":
        failed_key = "wallet"
        if prepare_ready or state in ("approving", "finalizing"):
            failed_key = "finalize"
        elif wallet_ready:
            failed_key = "prepare"
        checks[failed_key]["status"] = "failed"
        checks[failed_key]["reason"] = error_detail or "Onboarding failed."

    return checks


def _deposit_seed_address(wallet: Wallet) -> str:
    # Polymarket bridge requires the Polymarket wallet address as the seed.
    # This is the proxy/safe address, not the Privy EOA.
    return wallet.proxy_address or ""


def _get_positions_value(user_id: str) -> float:
    """Mark-to-market positions value from balance_poller cache (updated every 10s)."""
    from app.services.balance_poller import balance_poller
    return balance_poller.get(user_id).positions_value


def _funding_thresholds(usdc_balance: float, eth_balance: float, trading_balance: float | None, positions_value: float = 0.0) -> dict[str, float | bool]:
    required_usdc       = funding_service.compute_reserve_target(usdc_balance, trading_balance, positions_value)
    warn_usdc_threshold = funding_service.compute_warn_threshold(required_usdc)
    required_eth        = settings.FEE_WALLET_ETH_MIN
    warn_eth_threshold  = settings.FEE_WALLET_ETH_MIN_WARN
    suggested_usdc      = required_usdc   # show the real target, not a hardcoded number
    suggested_eth       = settings.FUNDING_ETH_MIN
    min_usdc            = required_usdc
    min_eth             = max(suggested_eth, required_eth)
    needs_usdc          = usdc_balance < settings.FEE_WALLET_USDC_STOP
    needs_eth           = eth_balance < required_eth
    warn_usdc           = not needs_usdc and usdc_balance < warn_usdc_threshold
    warn_eth            = not needs_eth and eth_balance < warn_eth_threshold
    wallet_funded       = not needs_usdc and not needs_eth
    return {
        "suggested_usdc": suggested_usdc,
        "suggested_eth": suggested_eth,
        "required_usdc": required_usdc,
        "required_eth": required_eth,
        "warn_usdc_threshold": warn_usdc_threshold,
        "warn_eth_threshold": warn_eth_threshold,
        "min_usdc": min_usdc,
        "min_eth": min_eth,
        "needs_usdc": needs_usdc,
        "needs_eth": needs_eth,
        "warn_usdc": warn_usdc,
        "warn_eth": warn_eth,
        "wallet_funded": wallet_funded,
    }


async def _ensure_cached_deposit_address(wallet: Wallet, db: DbSession) -> str:
    deposit_address = wallet.funding_deposit_address_cached or ""
    if deposit_address:
        return deposit_address
    seed_address = _deposit_seed_address(wallet)
    if not seed_address:
        raise BridgeClientError("Polymarket deposit seed address not available")
    deposit_addresses = await bridge_client.create_deposit_addresses(seed_address)
    address_block = deposit_addresses.get("address") or {}
    deposit_address = address_block.get("evm") or ""
    if not deposit_address:
        raise BridgeClientError("Bridge deposit address not available")
    wallet.funding_deposit_address_cached = deposit_address
    wallet.funding_deposit_cached_at = datetime.now(timezone.utc)
    await db.commit()
    return deposit_address



class WalletOut(BaseModel):
    wallet_id: str
    wallet_address: str
    proxy_address: str
    privy_wallet_id: str
    is_primary: bool
    usdc_balance: float | None = None


class FundingStatusOut(BaseModel):
    trading_balance: float | None = None
    usdc_balance: float | None
    eth_balance: float | None
    usdc_token_address: str | None = None
    wallet_funded: bool
    onboarding_complete: bool
    needs_usdc: bool
    needs_eth: bool
    warn_usdc: bool
    warn_eth: bool
    min_usdc: float
    min_eth: float
    suggested_usdc: float
    suggested_eth: float
    required_usdc: float
    required_eth: float
    fee_wallet_usdc_min: float
    fee_wallet_eth_min: float
    fee_wallet_usdc_min_warn: float
    fee_wallet_eth_min_warn: float
    fee_wallet_usdc_warn_threshold: float
    fee_wallet_eth_warn_threshold: float
    bridge_min_usdc: float
    bridgeable_usdc: float
    bridge_available: bool
    reserve_target: float
    fee_buffer_pct: float
    fee_wallet_usdc_stop: float
    funding_state: str
    funding_deposit_tx_hash: str | None = None
    funding_tx_hash: str | None = None
    funding_deposit_address_cached: str | None = None
    funding_bridge_started_at: str | None = None
    message: str
    positions_value: float = 0.0


@router.get("/onboarding-status")
async def onboarding_status(user: CurrentUser, db: DbSession):
    wallet = await _get_primary_wallet(user["sub"], db)
    if not wallet:
        return {
            "state": "no_wallet",
            "proxy_address": "",
            "has_clob_creds": False,
            "error_detail": "Wallet creation failed before onboarding could start.",
            "checks": _onboarding_checks(None),
        }
    return {
        "state": wallet.onboarding_state,
        "proxy_address": wallet.proxy_address or "",
        "has_clob_creds": bool(wallet.clob_api_key),
        "error_detail": wallet.onboarding_error_detail or "",
        "checks": _onboarding_checks(wallet),
    }


@router.get("/", response_model=list[WalletOut])
async def list_wallets(
    user: CurrentUser,
    db: DbSession,
):
    from app.services.balance_poller import balance_poller
    q = await db.execute(select(Wallet).where(Wallet.user_id == user["sub"]))
    wallets = q.scalars().all()
    cached = balance_poller.get(user["sub"])
    out: list[WalletOut] = []
    for wallet in wallets:
        balance = cached.trading_balance if wallet.is_primary else None
        out.append(WalletOut(
            wallet_id=wallet.id,
            wallet_address=wallet.wallet_address,
            proxy_address=wallet.proxy_address or "",
            privy_wallet_id=wallet.privy_wallet_id or "",
            is_primary=wallet.is_primary,
            usdc_balance=balance,
        ))
    return out


@router.get("/funding-status", response_model=FundingStatusOut)
async def funding_status(user: CurrentUser, db: DbSession):
    import asyncio, time
    from app.services.balance_poller import balance_poller

    wallet = await _get_primary_wallet(user["sub"], db)
    if not wallet:
        raise HTTPException(status_code=404, detail="Primary wallet not found")
    if not wallet.wallet_address or not wallet.privy_wallet_id:
        raise HTTPException(status_code=400, detail="Wallet not ready")

    # Instant cache read — poller refreshes every 10s
    cached = balance_poller.get(user["sub"])
    cache_age = time.monotonic() - cached.fetched_at

    usdc_balance    = cached.wallet_usdc    if cached.wallet_usdc    is not None else 0.0
    eth_balance     = cached.wallet_eth     if cached.wallet_eth     is not None else 0.0
    trading_balance = cached.trading_balance

    # Only force-refresh if cache is cold (never fetched yet)
    if cached.wallet_usdc is None and cached.wallet_eth is None:
        asyncio.create_task(
            balance_poller.refresh_user(user["sub"], wallet.wallet_address or "", wallet.proxy_address or "")
        )

    positions_value = _get_positions_value(user["sub"])
    thresholds = _funding_thresholds(usdc_balance, eth_balance, trading_balance, positions_value)
    suggested_usdc = float(thresholds["suggested_usdc"])
    suggested_eth = float(thresholds["suggested_eth"])
    required_usdc = float(thresholds["required_usdc"])
    required_eth = float(thresholds["required_eth"])
    warn_usdc_threshold = float(thresholds["warn_usdc_threshold"])
    warn_eth_threshold = float(thresholds["warn_eth_threshold"])
    min_usdc = float(thresholds["min_usdc"])
    min_eth = float(thresholds["min_eth"])
    needs_usdc = bool(thresholds["needs_usdc"])
    needs_eth = bool(thresholds["needs_eth"])
    warn_usdc = bool(thresholds["warn_usdc"])
    warn_eth = bool(thresholds["warn_eth"])
    wallet_funded = bool(thresholds["wallet_funded"])
    bridgeable_usdc = funding_service.compute_bridge_amount(usdc_balance, trading_balance, positions_value)
    bridge_available = bridgeable_usdc >= settings.BRIDGE_MIN_USDC

    message = ""
    if wallet.funding_state == "failed" and not ((trading_balance or 0.0) > 0):
        message = "Last bridge attempt failed. Use Fix funding to retry."
    elif wallet.funding_state in ("preparing", "bridging", "processing") and not ((trading_balance or 0.0) > 0):
        message = "Bridge in progress. Your trading balance should update automatically."
    elif needs_usdc and needs_eth:
        message = f"Funding required: add at least {required_usdc:.2f} USDC and {required_eth:.4f} ETH."
    elif needs_usdc:
        message = f"Funding required: add at least {required_usdc:.2f} USDC."
    elif needs_eth:
        message = f"Funding required: add at least {required_eth:.4f} ETH for gas."
    elif wallet_funded and not bridge_available:
        shortfall = max(0.0, settings.BRIDGE_MIN_USDC - bridgeable_usdc)
        message = (
            f"Bridge available from ${settings.BRIDGE_MIN_USDC:.2f}. "
            f"Add ${shortfall:.2f} more USDC above your reserve to bridge."
        )
    elif warn_usdc and warn_eth:
        message = f"Top-up suggested: keep at least {warn_usdc_threshold:.2f} USDC and {warn_eth_threshold:.4f} ETH."
    elif warn_usdc:
        message = f"Top-up suggested: keep at least {warn_usdc_threshold:.2f} USDC."
    elif warn_eth:
        message = f"Top-up suggested: keep at least {warn_eth_threshold:.4f} ETH."
    else:
        message = "Funding ready."  

    # Normalize observed balance if wallet spent funds (prevents stale baseline)
    last_obs = wallet.funding_last_usdc_observed or 0.0
    if usdc_balance < last_obs:
        wallet.funding_last_usdc_observed = usdc_balance
        last_obs = usdc_balance
        await db.commit()

    # Capture incoming deposit tx hash when a new deposit is detected
    if wallet_funded and (not wallet.funding_deposit_tx_hash or usdc_balance - last_obs >= required_usdc):
        deposit_tx = await funding_service.get_latest_usdc_deposit_tx(wallet.wallet_address)
        if deposit_tx and deposit_tx != wallet.funding_deposit_tx_hash:
            wallet.funding_deposit_tx_hash = deposit_tx
            await db.commit()

    # If trading balance shows funds, mark onboarding complete and clear failed states.
    if trading_balance is not None and trading_balance > 0:
        if not wallet.onboarding_complete:
            wallet.onboarding_complete = True
            wallet.onboarding_completed_at = datetime.now(timezone.utc)
        if wallet.funding_state != "completed":
            wallet.funding_state = "completed"
        # Confirm any submitted FundingTransactions (covers restart + normal flow)
        from sqlalchemy import update as _update
        await db.execute(
            _update(FundingTransaction)
            .where(FundingTransaction.wallet_id == wallet.id)
            .where(FundingTransaction.state == "submitted")
            .values(state="confirmed", completed_at=datetime.now(timezone.utc))
        )
        await db.commit()
        if needs_usdc or needs_eth:
            if needs_usdc and needs_eth:
                message = f"Trading balance ready. Top up wallet reserve to at least {required_usdc:.2f} USDC and {required_eth:.4f} ETH."
            elif needs_usdc:
                message = f"Trading balance ready. Top up wallet reserve to at least {required_usdc:.2f} USDC."
            else:
                message = f"Trading balance ready. Top up wallet reserve to at least {required_eth:.4f} ETH."
        else:
            if warn_usdc and warn_eth:
                message = f"Top-up suggested: keep at least {warn_usdc_threshold:.2f} USDC and {warn_eth_threshold:.4f} ETH."
            elif warn_usdc:
                message = f"Top-up suggested: keep at least {warn_usdc_threshold:.2f} USDC."
            elif warn_eth:
                message = f"Top-up suggested: keep at least {warn_eth_threshold:.4f} ETH."
            else:
                message = "Trading balance ready."

    # Timeout bridging if it takes too long (5 minutes)
    if wallet.funding_state in ("preparing", "bridging") and wallet.funding_bridge_started_at and not wallet.onboarding_complete:
        elapsed = datetime.now(timezone.utc) - wallet.funding_bridge_started_at
        if elapsed.total_seconds() >= 300:
            wallet.funding_state = "failed"
            await db.commit()

    auto_bridge_allowed = wallet.funding_state in ("idle", "completed")
    bridge_trigger_amount = required_usdc

    if FUNDING_ENABLED and wallet_funded and auto_bridge_allowed:
        last_obs = wallet.funding_last_usdc_observed or 0.0
        observed_delta = usdc_balance - last_obs
        should_auto_bridge = (
            observed_delta >= bridge_trigger_amount
            or bridge_available
        )
        log.info(
            "[funding:auto] user=%s state=%s wallet_funded=%s bridge_available=%s "
            "usdc=%.6f trading=%s last_obs=%.6f observed_delta=%.6f trigger=%.6f should_auto_bridge=%s",
            user["sub"],
            wallet.funding_state,
            wallet_funded,
            bridge_available,
            usdc_balance,
            f"{trading_balance:.6f}" if trading_balance is not None else "None",
            last_obs,
            observed_delta,
            bridge_trigger_amount,
            should_auto_bridge,
        )
        if should_auto_bridge:
            locked_wallet = await _get_primary_wallet(user["sub"], db, for_update=True)
            if locked_wallet and locked_wallet.funding_state in ("idle", "completed"):
                # Re-fetch live balance after acquiring lock to avoid race condition
                fresh_usdc, _ = await funding_service.get_balances(locked_wallet.wallet_address)
                fresh_usdc = fresh_usdc if fresh_usdc is not None else 0.0
                if fresh_usdc < required_usdc:
                    # Balance dropped below threshold between cache read and lock — abort
                    pass
                else:
                    wallet = locked_wallet
                    ft: FundingTransaction | None = None
                    try:
                        deposit_address = await _ensure_cached_deposit_address(wallet, db)
                        amount_to_bridge = funding_service.compute_bridge_amount(fresh_usdc, trading_balance, positions_value)
                        log.info(
                            "[funding:auto] user=%s fresh_usdc=%.6f amount_to_bridge=%.6f deposit=%s",
                            user["sub"],
                            fresh_usdc,
                            amount_to_bridge,
                            deposit_address,
                        )
                        if amount_to_bridge <= 0:
                            # Nothing to bridge — reset state, no FundingTransaction created
                            wallet.funding_state = "completed" if (trading_balance or 0.0) > 0 else "idle"
                            await db.commit()
                        else:
                            wallet.funding_state = "preparing"
                            wallet.funding_tx_hash = None
                            wallet.funding_bridge_started_at = datetime.now(timezone.utc)
                            ft = FundingTransaction(
                                id=str(uuid.uuid4()),
                                wallet_id=wallet.id,
                                amount_usdc=amount_to_bridge,
                                state="initiated",
                                source="auto",
                            )
                            db.add(ft)
                            await db.commit()
                            tx_hash = await transfer_usdc(
                                privy_wallet_id=wallet.privy_wallet_id,
                                wallet_address=wallet.wallet_address,
                                to_address=deposit_address,
                                amount_usdc=amount_to_bridge,
                            )
                            wallet.funding_state = "bridging"
                            wallet.funding_tx_hash = tx_hash
                            wallet.funding_last_usdc_observed = fresh_usdc
                            ft.state = "submitted"
                            ft.tx_hash = tx_hash
                            await db.commit()
                            log.info(
                                "[funding:auto] user=%s tx_submitted=%s amount=%.6f",
                                user["sub"],
                                tx_hash,
                                amount_to_bridge,
                            )
                            message = "Bridge in progress. Your trading balance should update automatically."
                    except (BridgeClientError, PrivyTxError) as e:
                        wallet.funding_state = "failed"
                        if ft is not None:
                            ft.state = "failed"
                            ft.error_detail = str(e)
                        await db.commit()
                        log.warning("[funding:auto] user=%s failed: %s", user["sub"], e)
                        message = f"Funding failed: {e}"

    return FundingStatusOut(
        trading_balance=round((trading_balance or 0.0) + positions_value, 2) if trading_balance is not None else None,
        usdc_balance=usdc_balance,
        eth_balance=eth_balance,
        usdc_token_address=settings.ARBITRUM_USDC_ADDRESS or None,
        wallet_funded=wallet_funded,
        onboarding_complete=wallet.onboarding_complete,
        needs_usdc=needs_usdc,
        needs_eth=needs_eth,
        warn_usdc=warn_usdc,
        warn_eth=warn_eth,
        min_usdc=min_usdc,
        min_eth=min_eth,
        suggested_usdc=suggested_usdc,
        suggested_eth=suggested_eth,
        required_usdc=required_usdc,
        required_eth=required_eth,
        fee_wallet_usdc_min=settings.FEE_WALLET_USDC_MIN,
        fee_wallet_eth_min=settings.FEE_WALLET_ETH_MIN,
        fee_wallet_usdc_min_warn=warn_usdc_threshold,
        fee_wallet_eth_min_warn=settings.FEE_WALLET_ETH_MIN_WARN,
        fee_wallet_usdc_warn_threshold=warn_usdc_threshold,
        fee_wallet_eth_warn_threshold=warn_eth_threshold,
        bridge_min_usdc=settings.BRIDGE_MIN_USDC,
        bridgeable_usdc=bridgeable_usdc,
        bridge_available=bridge_available,
        reserve_target=funding_service.compute_reserve_target(usdc_balance, trading_balance, positions_value),
        fee_buffer_pct=settings.FEE_BUFFER_PCT,
        fee_wallet_usdc_stop=settings.FEE_WALLET_USDC_STOP,
        funding_state=wallet.funding_state,
        funding_deposit_tx_hash=wallet.funding_deposit_tx_hash,
        funding_tx_hash=wallet.funding_tx_hash,
        funding_deposit_address_cached=wallet.funding_deposit_address_cached,
        funding_bridge_started_at=wallet.funding_bridge_started_at.isoformat() if wallet.funding_bridge_started_at else None,
        message=message,
        positions_value=round(positions_value, 2),
    )


@router.post("/funding-force", response_model=FundingStatusOut)
async def funding_force(user: CurrentUser, db: DbSession):
    wallet = await _get_primary_wallet(user["sub"], db, for_update=True)
    if not wallet:
        raise HTTPException(status_code=404, detail="Primary wallet not found")
    if not wallet.wallet_address or not wallet.privy_wallet_id:
        raise HTTPException(status_code=400, detail="Wallet not ready")

    from app.services.balance_poller import balance_poller
    await balance_poller.refresh_user(user["sub"], wallet.wallet_address or "", wallet.proxy_address or "")
    cached = balance_poller.get(user["sub"])
    usdc_balance    = cached.wallet_usdc    or 0.0
    eth_balance     = cached.wallet_eth     or 0.0
    trading_balance = cached.trading_balance
    positions_value_force = _get_positions_value(user["sub"])
    thresholds = _funding_thresholds(usdc_balance, eth_balance, trading_balance, positions_value_force)
    required_usdc = float(thresholds["required_usdc"])
    required_eth = float(thresholds["required_eth"])
    if usdc_balance < required_usdc or eth_balance < required_eth:
        raise HTTPException(status_code=400, detail="Wallet funding requirements not met")

    if wallet.funding_state == "bridging" and wallet.funding_bridge_started_at:
        elapsed = datetime.now(timezone.utc) - wallet.funding_bridge_started_at
        if elapsed.total_seconds() < 300:
            raise HTTPException(status_code=409, detail="Bridge already in progress")

    ft = FundingTransaction(
        id=str(uuid.uuid4()),
        wallet_id=wallet.id,
        amount_usdc=0.0,
        state="initiated",
        source="manual",
    )
    db.add(ft)

    try:
        deposit_address = await _ensure_cached_deposit_address(wallet, db)
        wallet.funding_state = "preparing"
        wallet.funding_tx_hash = None
        wallet.funding_bridge_started_at = datetime.now(timezone.utc)
        await db.commit()

        amount_to_bridge = funding_service.compute_bridge_amount(usdc_balance, trading_balance, positions_value_force)
        ft.amount_usdc = amount_to_bridge
        if amount_to_bridge <= 0:
            wallet.funding_state = "completed" if (trading_balance or 0.0) > 0 else "idle"
            ft.state = "failed"
            ft.error_detail = "Bridge amount too small after reserve"
            await db.commit()
            raise HTTPException(status_code=400, detail="Insufficient bridgeable USDC after keeping reserve")

        tx_hash = await transfer_usdc(
            privy_wallet_id=wallet.privy_wallet_id,
            wallet_address=wallet.wallet_address,
            to_address=deposit_address,
            amount_usdc=amount_to_bridge,
        )

        wallet.funding_state = "bridging"
        wallet.funding_tx_hash = tx_hash
        wallet.funding_last_usdc_observed = usdc_balance
        ft.state = "submitted"
        ft.tx_hash = tx_hash
        await db.commit()
    except (BridgeClientError, PrivyTxError) as e:
        wallet.funding_state = "failed"
        ft.state = "failed"
        ft.error_detail = str(e)
        await db.commit()
        raise HTTPException(status_code=502, detail=str(e))

    return await funding_status(user, db)


class FundingTxOut(BaseModel):
    id: str
    amount_usdc: float
    state: str
    tx_hash: str | None = None
    source: str
    error_detail: str | None = None
    initiated_at: str
    completed_at: str | None = None


@router.get("/bridge-history", response_model=list[FundingTxOut])
async def bridge_history(user: CurrentUser, db: DbSession):
    wallet = await _get_primary_wallet(user["sub"], db)
    if not wallet:
        raise HTTPException(status_code=404, detail="Primary wallet not found")

    q = await db.execute(
        select(FundingTransaction)
        .where(FundingTransaction.wallet_id == wallet.id)
        .order_by(FundingTransaction.initiated_at.desc())
        .limit(50)
    )
    txs = q.scalars().all()
    return [
        FundingTxOut(
            id=tx.id,
            amount_usdc=tx.amount_usdc,
            state=tx.state,
            tx_hash=tx.tx_hash,
            source=tx.source,
            error_detail=tx.error_detail,
            initiated_at=tx.initiated_at.isoformat(),
            completed_at=tx.completed_at.isoformat() if tx.completed_at else None,
        )
        for tx in txs
    ]
