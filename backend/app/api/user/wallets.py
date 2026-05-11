"""
user/wallets.py - User wallet endpoints.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.db.models import Wallet, FundingTransaction
from app.integrations.polymarket.bridge_client import bridge_client, BridgeClientError

router = APIRouter(prefix="/wallets", tags=["user:wallets"])
FUNDING_ENABLED = False  # auto-bridge disabled — users deposit directly to Polymarket
BUILDER_FEE_BPS = 40     # informational only — not charged by app
log = logging.getLogger("uvicorn.error")


def estimate_builder_fee(volume_usdc: float) -> float:
    return round(volume_usdc * BUILDER_FEE_BPS / 10_000, 6)



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


def _get_positions_value(user_id: str) -> tuple[float, float]:
    """Returns (positions_value, redeem_value) from balance_poller cache (updated every 10s)."""
    from app.services.balance_poller import balance_poller
    bal = balance_poller.get(user_id)
    return bal.positions_value, bal.redeem_value



async def _ensure_cached_deposit_addresses(wallet: Wallet, db: DbSession) -> dict[str, str]:
    """Return {evm, svm} deposit addresses, fetching from Polymarket API if not cached."""
    evm = wallet.deposit_address_evm or wallet.funding_deposit_address_cached or ""
    svm = wallet.deposit_address_svm or ""

    if evm and svm:
        return {"evm": evm, "svm": svm}

    seed_address = _deposit_seed_address(wallet)
    if not seed_address:
        raise BridgeClientError("Polymarket account not ready yet (proxy_address missing)")

    data = await bridge_client.create_deposit_addresses(seed_address)
    address_block = data.get("address") or {}
    new_evm = address_block.get("evm") or ""
    new_svm = address_block.get("svm") or ""

    if not evm and new_evm:
        wallet.deposit_address_evm = new_evm
        evm = new_evm
    if not svm and new_svm:
        wallet.deposit_address_svm = new_svm
        svm = new_svm

    wallet.deposit_addresses_cached_at = datetime.now(timezone.utc)

    # Keep legacy column in sync for compat
    if new_evm and not wallet.funding_deposit_address_cached:
        wallet.funding_deposit_address_cached = new_evm
        wallet.funding_deposit_cached_at = datetime.now(timezone.utc)

    await db.commit()
    return {"evm": evm, "svm": svm}


# Supported chains/tokens for deposit UI
_DEPOSIT_CHAINS = [
    {"chain_id": "42161", "chain_name": "Arbitrum", "address_type": "evm",
     "tokens": ["ETH", "USDC"]},
    {"chain_id": "8453",  "chain_name": "Base",     "address_type": "evm",
     "tokens": ["ETH", "USDC"]},
    {"chain_id": "solana","chain_name": "Solana",   "address_type": "svm",
     "tokens": ["SOL", "USDC"]},
]

_ALLOWED_CHAINS = {"42161", "8453", "solana"}
_ALLOWED_TOKENS = {"ETH", "USDC", "SOL"}

_supported_assets_cache: dict[str, Any] = {}
_supported_assets_cached_at: float = 0.0
_ASSETS_CACHE_TTL = 3600.0  # 1 hour


async def _get_supported_assets() -> list[dict]:
    """Fetch and cache Polymarket supported assets, filtered to known chains/tokens."""
    import time
    global _supported_assets_cache, _supported_assets_cached_at
    now = time.monotonic()
    if _supported_assets_cache and now - _supported_assets_cached_at < _ASSETS_CACHE_TTL:
        return _supported_assets_cache.get("chains", [])

    try:
        raw = await bridge_client.get_supported_assets()
        assets = raw if isinstance(raw, list) else raw.get("assets", raw.get("data", []))
        chains_out: list[dict] = []
        for chain_cfg in _DEPOSIT_CHAINS:
            chain_id = chain_cfg["chain_id"]
            addr_type = chain_cfg["address_type"]
            tokens_out = []
            for token_sym in chain_cfg["tokens"]:
                token_data: dict | None = None
                for asset in assets:
                    a_chain = str(asset.get("chainId") or asset.get("chain_id") or "")
                    a_sym = (asset.get("symbol") or asset.get("token", {}).get("symbol") or "").upper()
                    if a_chain == chain_id and a_sym == token_sym:
                        token_data = asset
                        break
                token_entry: dict = {"symbol": token_sym, "address": "", "decimals": 6 if token_sym == "USDC" else 18, "min_checkout_usd": None}
                if token_data:
                    tok = token_data.get("token") or token_data
                    token_entry["address"] = tok.get("address") or tok.get("tokenAddress") or ""
                    token_entry["decimals"] = int(tok.get("decimals") or token_entry["decimals"])
                    min_usd = token_data.get("minCheckoutUsd") or token_data.get("min_checkout_usd")
                    if min_usd is not None:
                        token_entry["min_checkout_usd"] = float(min_usd)
                tokens_out.append(token_entry)
            chains_out.append({
                "chain_id": chain_id,
                "chain_name": chain_cfg["chain_name"],
                "address_type": addr_type,
                "tokens": tokens_out,
            })
        _supported_assets_cache = {"chains": chains_out}
        _supported_assets_cached_at = now
        return chains_out
    except Exception as e:
        log.warning("[deposit_config] supported_assets fetch failed: %s", e)
        # Return minimal fallback without min_checkout_usd
        return [
            {"chain_id": c["chain_id"], "chain_name": c["chain_name"],
             "address_type": c["address_type"],
             "tokens": [{"symbol": t, "address": "", "decimals": 6 if t == "USDC" else 18, "min_checkout_usd": None} for t in c["tokens"]]}
            for c in _DEPOSIT_CHAINS
        ]



class DepositAddressesOut(BaseModel):
    evm: str = ""
    svm: str = ""


class DepositTokenOut(BaseModel):
    symbol: str
    address: str = ""
    decimals: int = 6
    min_checkout_usd: float | None = None


class DepositChainOut(BaseModel):
    chain_id: str
    chain_name: str
    address_type: str   # "evm" | "svm"
    tokens: list[DepositTokenOut]


class DepositConfigOut(BaseModel):
    polymarket_address: str
    deposit_addresses: DepositAddressesOut
    chains: list[DepositChainOut]
    trading_balance: float | None = None
    trading_cash_available: float | None = None  # balance minus open positions
    positions_value: float = 0.0
    redeem_value: float = 0.0
    account_ready: bool
    onboarding_state: str
    onboarding_complete: bool
    is_funded_now: bool


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
    redeem_value: float = 0.0


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


@router.get("/deposit-config", response_model=DepositConfigOut)
async def deposit_config(user: CurrentUser, db: DbSession):
    """
    Returns Polymarket deposit addresses (EVM + SVM), supported chains/tokens,
    and current trading balance. No auto-bridge triggered here.
    If Polymarket API fails for address fetch, returns error with deposit_unavailable flag.
    """
    from app.services.balance_poller import balance_poller

    wallet = await _get_primary_wallet(user["sub"], db)
    if not wallet:
        raise HTTPException(status_code=404, detail="Primary wallet not found")

    account_ready = bool(
        wallet.onboarding_state == "ready"
        and wallet.proxy_address
        and wallet.clob_api_key
    )

    cached = balance_poller.get(user["sub"])
    trading_balance = cached.trading_balance
    positions_value, redeem_value = _get_positions_value(user["sub"])
    trading_cash = (trading_balance or 0.0)  # cash not in positions
    is_funded_now = (trading_balance is not None and trading_balance > 0)

    # Lazy backfill onboarding_complete
    if is_funded_now and not wallet.onboarding_complete:
        wallet.onboarding_complete = True
        wallet.onboarding_completed_at = datetime.now(timezone.utc)
        await db.commit()

    deposit_addresses: dict[str, str] = {"evm": "", "svm": ""}
    chains: list[dict] = []
    deposit_error: str | None = None

    if account_ready:
        try:
            deposit_addresses = await _ensure_cached_deposit_addresses(wallet, db)
            chains = await _get_supported_assets()
        except BridgeClientError as e:
            deposit_error = str(e)
            log.warning("[deposit_config] deposit address fetch failed: %s", e)

    chains_out = [
        DepositChainOut(
            chain_id=c["chain_id"],
            chain_name=c["chain_name"],
            address_type=c["address_type"],
            tokens=[DepositTokenOut(**t) for t in c["tokens"]],
        )
        for c in chains
    ]

    return DepositConfigOut(
        polymarket_address=wallet.proxy_address or "",
        deposit_addresses=DepositAddressesOut(**deposit_addresses),
        chains=chains_out,
        trading_balance=round(trading_balance, 2) if trading_balance is not None else None,
        trading_cash_available=round(trading_cash, 2) if trading_balance is not None else None,
        positions_value=round(positions_value, 2),
        redeem_value=round(redeem_value, 2),
        account_ready=account_ready,
        onboarding_state=wallet.onboarding_state,
        onboarding_complete=wallet.onboarding_complete,
        is_funded_now=is_funded_now,
    )


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
    """
    Legacy compat endpoint. Returns neutral values for Arbitrum fields (no auto-bridge).
    Use /deposit-config for the new deposit flow.
    """
    from app.services.balance_poller import balance_poller

    wallet = await _get_primary_wallet(user["sub"], db)
    if not wallet:
        raise HTTPException(status_code=404, detail="Primary wallet not found")

    cached = balance_poller.get(user["sub"])
    trading_balance = cached.trading_balance
    positions_value, redeem_value = _get_positions_value(user["sub"])

    # Lazy backfill onboarding_complete when trading balance observed
    if trading_balance is not None and trading_balance > 0:
        if not wallet.onboarding_complete:
            wallet.onboarding_complete = True
            wallet.onboarding_completed_at = datetime.now(timezone.utc)
            await db.commit()

    is_funded = trading_balance is not None and trading_balance > 0
    message = "Trading balance ready." if is_funded else "Deposit USDC to your Polymarket account to start trading."

    return FundingStatusOut(
        trading_balance=round((trading_balance or 0.0) + positions_value, 2) if trading_balance is not None else None,
        usdc_balance=0.0,
        eth_balance=0.0,
        usdc_token_address=None,
        wallet_funded=is_funded,
        onboarding_complete=wallet.onboarding_complete,
        needs_usdc=False,
        needs_eth=False,
        warn_usdc=False,
        warn_eth=False,
        min_usdc=0.0,
        min_eth=0.0,
        suggested_usdc=0.0,
        suggested_eth=0.0,
        required_usdc=0.0,
        required_eth=0.0,
        fee_wallet_usdc_min=0.0,
        fee_wallet_eth_min=0.0,
        fee_wallet_usdc_min_warn=0.0,
        fee_wallet_eth_min_warn=0.0,
        fee_wallet_usdc_warn_threshold=0.0,
        fee_wallet_eth_warn_threshold=0.0,
        bridge_min_usdc=0.0,
        bridgeable_usdc=0.0,
        bridge_available=False,
        reserve_target=0.0,
        fee_buffer_pct=0.0,
        fee_wallet_usdc_stop=0.0,
        funding_state=wallet.funding_state,
        funding_deposit_tx_hash=wallet.funding_deposit_tx_hash,
        funding_tx_hash=None,
        funding_deposit_address_cached=wallet.deposit_address_evm or wallet.funding_deposit_address_cached,
        funding_bridge_started_at=None,
        message=message,
        positions_value=round(positions_value, 2),
        redeem_value=round(redeem_value, 2),
    )


@router.post("/funding-force", response_model=FundingStatusOut)
async def funding_force(user: CurrentUser, db: DbSession):
    """No-op — auto-bridge removed. Returns same response as funding-status."""
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
