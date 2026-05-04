"""
redeem_service.py — Redeem winning CTF tokens via Safe + Polymarket relayer.
"""
from __future__ import annotations

import logging

import httpx
from eth_abi import encode as abi_encode
from eth_utils import to_checksum_address

from app.core.config import settings
from app.services.polymarket_onboarding_service import (
    RELAYER_URL,
    CTF,
    PUSD,
    ZERO_ADDRESS,
    CHAIN_ID,
    _relayer_post_authed,
    _get_safe_nonce,
    _sign_safe_tx,
    _poll_transaction,
)

log = logging.getLogger("redeem")

# redeemPositions(address,bytes32,bytes32,uint256[]) selector
REDEEM_SELECTOR = bytes.fromhex("01b7037c")

# Try each collateral until one succeeds (PRECHECK_SKIPPED = wrong collateral, try next)
_COLLATERALS = [
    "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB",  # pUSD (V2)
    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e (V1)
    "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2",  # WCOL (neg-risk)
]


def _encode_redeem_positions(condition_id_hex: str, collateral: str) -> str:
    coll = to_checksum_address(collateral)
    parent_collection = bytes(32)
    cid = condition_id_hex.lower().lstrip("0x").zfill(64)
    condition_id_bytes = bytes.fromhex(cid)
    encoded = abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [coll, parent_collection, condition_id_bytes, [1, 2]],
    )
    return "0x" + REDEEM_SELECTOR.hex() + encoded.hex()


async def redeem_positions(
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    condition_id: str,
    estimated_tokens: float = 0.0,
    known_token_id: str | None = None,
) -> dict:
    result: dict = {
        "ok": False,
        "tx_id": None,
        "tx_hash": None,
        "condition_id": condition_id,
        "estimated_usdc": round(estimated_tokens, 2),
        "error": None,
    }
    log.info(
        "redeem START: safe=%s condition=%s estimated=%.4f",
        safe_address[:12], condition_id[:20], estimated_tokens,
    )

    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as http:
        for collateral in _COLLATERALS:
            try:
                calldata = _encode_redeem_positions(condition_id, collateral)
                nonce = await _get_safe_nonce(http, safe_address)
                sig = await _sign_safe_tx(
                    privy_wallet_id, safe_address,
                    CTF, calldata, "0", 0, nonce,
                )
                body = {
                    "from": eoa_address,
                    "to": CTF,
                    "proxyWallet": safe_address,
                    "data": calldata,
                    "nonce": nonce,
                    "signature": sig,
                    "signatureParams": {
                        "gasPrice": "0",
                        "operation": "0",
                        "safeTxnGas": "0",
                        "baseGas": "0",
                        "gasToken": ZERO_ADDRESS,
                        "refundReceiver": ZERO_ADDRESS,
                    },
                    "type": "SAFE",
                    "metadata": f"redeemPositions conditionId={condition_id[:20]}",
                }
                resp = await _relayer_post_authed(http, "/submit", body)
                tx_id = resp.get("transactionID")
                if not tx_id:
                    log.warning("redeem no tx_id collateral=%s resp=%s", collateral[:12], resp)
                    continue

                log.info("redeem polling tx_id=%s collateral=%s", tx_id, collateral[:12])
                mined = await _poll_transaction(http, tx_id, max_polls=40)
                if mined:
                    result["ok"] = True
                    result["tx_id"] = tx_id
                    result["tx_hash"] = mined.get("transactionHash")
                    log.info("redeem MINED collateral=%s tx_hash=%s usdc~=%.2f",
                             collateral[:12], result["tx_hash"], estimated_tokens)
                    return result

                # STATE_FAILED — check if PRECHECK_SKIPPED (wrong collateral) or real failure
                log.warning("redeem STATE_FAILED collateral=%s — trying next", collateral[:12])

            except Exception as e:
                log.warning("redeem error collateral=%s: %s", collateral[:12], e)

    result["error"] = "all collaterals failed (PRECHECK_SKIPPED or timeout)"
    log.error("redeem ALL FAILED condition=%s", condition_id[:20])
    return result
