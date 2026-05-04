"""
redeem_service.py — Redeem winning CTF tokens via Safe + Polymarket relayer.
Detects collateral on-chain (like poly-web3) — single relayer submit per condition.
"""
from __future__ import annotations

import asyncio
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

REDEEM_SELECTOR = bytes.fromhex("01b7037c")
POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"

_COLLATERALS = [
    "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2",  # WCOL (neg-risk, most common)
    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e (V1)
    "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB",  # pUSD (V2)
]

# getCollectionId / getPositionId selectors
_SEL_GC = "856296f7"  # getCollectionId(bytes32,bytes32,uint256)
_SEL_GP = "39dd7530"  # getPositionId(address,bytes32)


async def _rpc_call(http: httpx.AsyncClient, data: str) -> str:
    resp = await http.post(
        POLYGON_RPC,
        json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
              "params": [{"to": CTF, "data": data}, "latest"]},
    )
    return resp.json().get("result", "0x")


async def _detect_collateral(
    http: httpx.AsyncClient,
    condition_id: str,
    token_id: str,
    outcome: str,
) -> tuple[str, int] | None:
    """
    Call getPositionId on-chain for each collateral candidate.
    Returns (collateral_address, index_set) or None if not found.
    """
    try:
        target = int(token_id)
    except (ValueError, TypeError):
        return None

    parent = bytes(32)
    cid = bytes.fromhex(condition_id.lstrip("0x").zfill(64))

    o = outcome.upper().strip()
    if o in ("YES", "TRUE", "1"):
        index_sets = [1]
    elif o in ("NO", "FALSE", "0"):
        index_sets = [2]
    else:
        index_sets = [1, 2]

    for collateral in _COLLATERALS:
        for idx in index_sets:
            gc_args = abi_encode(["bytes32", "bytes32", "uint256"], [parent, cid, idx])
            coll_hex = await _rpc_call(http, "0x" + _SEL_GC + gc_args.hex())
            coll_bytes = bytes.fromhex(coll_hex[2:].zfill(64))

            gp_args = abi_encode(["address", "bytes32"], [to_checksum_address(collateral), coll_bytes])
            pos_hex = await _rpc_call(http, "0x" + _SEL_GP + gp_args.hex())
            pos_id = int(pos_hex, 16) if pos_hex and pos_hex != "0x" else 0

            if pos_id == target:
                log.info("redeem collateral=%s idx=%d matches token_id", collateral[:12], idx)
                return collateral, idx

    log.warning("redeem: no collateral match for condition=%s token=%s", condition_id[:20], str(token_id)[:20])
    return None


def _encode_calldata(condition_id: str, collateral: str, index_set: int) -> str:
    coll = to_checksum_address(collateral)
    parent = bytes(32)
    cid = bytes.fromhex(condition_id.lstrip("0x").zfill(64))
    encoded = abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [coll, parent, cid, [index_set]],
    )
    return "0x" + REDEEM_SELECTOR.hex() + encoded.hex()


async def redeem_positions(
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    condition_id: str,
    estimated_tokens: float = 0.0,
    known_token_id: str | None = None,
    outcome: str = "",
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
            # Detect correct collateral + indexSet on-chain
            match = await _detect_collateral(http, condition_id, known_token_id or "", outcome)
            if not match:
                result["error"] = "collateral not found on-chain"
                log.error("redeem no collateral match condition=%s", condition_id[:20])
                return result

            collateral, index_set = match
            calldata = _encode_calldata(condition_id, collateral, index_set)

            nonce = await _get_safe_nonce(http, safe_address)
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
                "metadata": f"redeemPositions conditionId={condition_id[:20]}",
            }

            resp = await _relayer_post_authed(http, "/submit", body)
            tx_id = resp.get("transactionID")
            if not tx_id:
                result["error"] = f"no tx_id from relayer: {resp}"
                return result

            result["tx_id"] = tx_id
            log.info("redeem polling tx_id=%s", tx_id)
            mined = await _poll_transaction(http, tx_id, max_polls=40)
            if mined:
                result["ok"] = True
                result["tx_hash"] = mined.get("transactionHash")
                log.info("redeem MINED tx_hash=%s usdc~=%.2f", result["tx_hash"], estimated_tokens)
            else:
                result["error"] = "STATE_FAILED or timeout"
                log.error("redeem FAILED tx_id=%s condition=%s", tx_id, condition_id[:20])

    except Exception as e:
        result["error"] = str(e)
        log.exception("redeem EXCEPTION condition=%s: %s", condition_id[:20], e)

    return result
