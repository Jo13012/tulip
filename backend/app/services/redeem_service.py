"""
redeem_service.py — Redeem winning CTF tokens via Safe + Polymarket relayer.
Logic mirrors poly-web3: binary markets use USDC CTF, neg-risk markets use NegRiskAdapter.
"""
from __future__ import annotations

import logging

import httpx
from eth_abi import encode as abi_encode
from eth_utils import to_checksum_address

from app.core.config import settings
from app.services.polymarket_onboarding_service import (
    CTF,
    ZERO_ADDRESS,
    _relayer_post_authed,
    _get_safe_nonce,
    _sign_safe_tx,
    _poll_transaction,
)

log = logging.getLogger("redeem")

# Selectors
REDEEM_SELECTOR          = bytes.fromhex("01b7037c")  # CTF redeemPositions(address,bytes32,bytes32,uint256[])
NEG_RISK_REDEEM_SELECTOR = bytes.fromhex("01b7037c")  # same selector, different ABI

# Addresses (Polygon mainnet)
PUSD        = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # pUSD — V2 collateral (replaced USDC.e Apr 2026)
USDC_E      = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e — kept for old positions
NEG_RISK    = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"  # NegRiskAdapter

POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"


async def _is_neg_risk(http: httpx.AsyncClient, condition_id: str) -> bool:
    """Check gamma API to determine if this is a neg-risk market."""
    try:
        resp = await http.get(
            "https://gamma-api.polymarket.com/markets",
            params={"conditionId": condition_id, "limit": 1},
            timeout=8.0,
        )
        data = resp.json()
        if isinstance(data, list) and data:
            m = data[0]
            if m.get("conditionId", "").lower() == condition_id.lower():
                return bool(m.get("negRisk", False))
    except Exception:
        pass
    return False


async def _try_binary_redeem(
    http: httpx.AsyncClient,
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    condition_id: str,
    collateral: str,
    nonce: str,
) -> dict | None:
    """Submit one binary redeem attempt. Returns mined tx or None (incl. PRECHECK_SKIPPED)."""
    calldata = _encode_binary_redeem(condition_id, collateral)
    sig = await _sign_safe_tx(privy_wallet_id, safe_address, CTF, calldata, "0", 0, nonce)
    body = {
        "from": eoa_address,
        "to": CTF,
        "proxyWallet": safe_address,
        "data": calldata,
        "nonce": nonce,
        "signature": sig,
        "signatureParams": {
            "gasPrice": "0", "operation": "0",
            "safeTxnGas": "0", "baseGas": "0",
            "gasToken": ZERO_ADDRESS, "refundReceiver": ZERO_ADDRESS,
        },
        "type": "SAFE",
    }
    try:
        resp = await _relayer_post_authed(http, "/submit", body)
    except Exception as e:
        log.warning("redeem submit error collateral=%s: %s", collateral[-6:], e)
        return None
    tx_id = resp.get("transactionID")
    if not tx_id:
        return None
    log.info("redeem polling collateral=%s tx_id=%s", "pUSD" if collateral == PUSD else "USDC.e", tx_id)
    return await _poll_transaction(http, tx_id, max_polls=30)


def _encode_binary_redeem(condition_id: str, collateral: str | None = None) -> str:
    """Binary market: redeemPositions(collateral, 0x0, conditionId, [1,2])"""
    if collateral is None:
        collateral = PUSD
    cid = bytes.fromhex(condition_id.lstrip("0x").zfill(64))
    encoded = abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [to_checksum_address(collateral), bytes(32), cid, [1, 2]],
    )
    return "0x" + REDEEM_SELECTOR.hex() + encoded.hex()


def _encode_neg_risk_redeem(condition_id: str, size: float) -> str:
    """Neg-risk: NegRiskAdapter.redeemPositions(conditionId, [yes_amount, no_amount])"""
    cid = bytes.fromhex(condition_id.lstrip("0x").zfill(64))
    # amounts in base units (1e6), pass full size for both slots — contract handles payout
    amount = int(size * 1_000_000)
    encoded = abi_encode(
        ["bytes32", "uint256[]"],
        [cid, [amount, amount]],
    )
    # NegRiskAdapter.redeemPositions(bytes32,uint256[]) selector
    from eth_hash.auto import keccak
    sel = keccak(b"redeemPositions(bytes32,uint256[])")[:4]
    return "0x" + sel.hex() + encoded.hex()


async def _submit_and_poll(
    http: httpx.AsyncClient,
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    to_contract: str,
    calldata: str,
    nonce: str | None = None,
) -> dict | None:
    """Sign via Safe, submit to relayer, poll. Returns mined tx or None."""
    if nonce is None:
        nonce = await _get_safe_nonce(http, safe_address)
    sig = await _sign_safe_tx(privy_wallet_id, safe_address, to_contract, calldata, "0", 0, nonce)
    body = {
        "from": eoa_address,
        "to": to_contract,
        "proxyWallet": safe_address,
        "data": calldata,
        "nonce": nonce,
        "signature": sig,
        "signatureParams": {
            "gasPrice": "0", "operation": "0",
            "safeTxnGas": "0", "baseGas": "0",
            "gasToken": ZERO_ADDRESS, "refundReceiver": ZERO_ADDRESS,
        },
        "type": "SAFE",
    }
    resp = await _relayer_post_authed(http, "/submit", body)
    tx_id = resp.get("transactionID")
    if not tx_id:
        log.warning("redeem no tx_id: %s", resp)
        return None
    log.info("redeem polling tx_id=%s", tx_id)
    return await _poll_transaction(http, tx_id, max_polls=40)


async def redeem_positions(
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    condition_id: str,
    estimated_tokens: float = 0.0,
    known_token_id: str | None = None,
    outcome: str = "",
    nonce: str | None = None,
) -> dict:
    result: dict = {
        "ok": False,
        "tx_id": None,
        "tx_hash": None,
        "condition_id": condition_id,
        "estimated_usdc": round(estimated_tokens, 2),
        "error": None,
    }
    log.info("redeem START condition=%s outcome=%s estimated=%.2f",
             condition_id[:20], outcome, estimated_tokens)

    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as http:
            neg_risk = await _is_neg_risk(http, condition_id)
            log.info("redeem neg_risk=%s condition=%s", neg_risk, condition_id[:20])

            if neg_risk:
                calldata = _encode_neg_risk_redeem(condition_id, estimated_tokens)
                to_contract = NEG_RISK
                mined = await _submit_and_poll(
                    http, privy_wallet_id, eoa_address, safe_address,
                    to_contract, calldata, nonce=nonce,
                )
            else:
                # Try pUSD first, then USDC.e — relayer PRECHECK_SKIPS wrong collateral
                # so only one will actually mine. Nonce increments after each submit.
                cur_nonce = nonce or await _get_safe_nonce(http, safe_address)
                mined = await _try_binary_redeem(
                    http, privy_wallet_id, eoa_address, safe_address,
                    condition_id, PUSD, cur_nonce,
                )
                if not mined:
                    log.info("redeem pUSD skipped/failed, trying USDC.e condition=%s", condition_id[:20])
                    # Nonce only increments on-chain if tx actually executed — re-fetch to be safe
                    cur_nonce = await _get_safe_nonce(http, safe_address)
                    mined = await _try_binary_redeem(
                        http, privy_wallet_id, eoa_address, safe_address,
                        condition_id, USDC_E, cur_nonce,
                    )

            if mined:
                result["ok"] = True
                result["tx_hash"] = mined.get("transactionHash")
                log.info("redeem MINED tx_hash=%s usdc~=%.2f", result["tx_hash"], estimated_tokens)
            else:
                result["error"] = "STATE_FAILED or timeout on both collaterals"
                log.error("redeem FAILED condition=%s", condition_id[:20])

    except Exception as e:
        result["error"] = str(e)
        log.exception("redeem EXCEPTION condition=%s: %s", condition_id[:20], e)

    return result
