"""
redeem_service.py — Redeem winning CTF tokens via Safe + Polymarket relayer.

After a market resolves, winning tokens can be redeemed 1:1 for pUSD.
This submits redeemPositions() on the CTF contract through the user's Safe.
"""
from __future__ import annotations

import json
import logging

import httpx
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

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
    _builder_headers,
)

log = logging.getLogger("redeem")

# redeemPositions(address,bytes32,bytes32,uint256[]) selector
REDEEM_SELECTOR = bytes.fromhex("01b7037c")


def _encode_redeem_positions(condition_id_hex: str) -> str:
    """
    ABI-encode redeemPositions(collateralToken, parentCollectionId, conditionId, indexSets).

    collateralToken    = pUSD
    parentCollectionId = 0x000...000
    conditionId        = condition_id_hex (32 bytes)
    indexSets          = [1, 2]  (redeem both slots — only winner pays)
    """
    collateral = to_checksum_address(PUSD)
    parent_collection = bytes(32)  # 32 zero bytes

    cid = condition_id_hex.lower()
    if cid.startswith("0x"):
        cid = cid[2:]
    condition_id_bytes = bytes.fromhex(cid.zfill(64))

    index_sets = [1, 2]

    encoded = abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [collateral, parent_collection, condition_id_bytes, index_sets],
    )
    return "0x" + REDEEM_SELECTOR.hex() + encoded.hex()


async def redeem_positions(
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    condition_id: str,
    estimated_tokens: float = 0.0,
) -> dict:
    """
    Submit redeemPositions() for a single condition through the user's Safe.

    Returns:
        {"ok": bool, "tx_id": str|None, "tx_hash": str|None,
         "condition_id": str, "estimated_usdc": float, "error": str|None}
    """
    result: dict = {
        "ok": False,
        "tx_id": None,
        "tx_hash": None,
        "condition_id": condition_id,
        "estimated_usdc": round(estimated_tokens, 2),
        "error": None,
    }
    try:
        calldata = _encode_redeem_positions(condition_id)

        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as http:
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
                "metadata": f"redeemPositions conditionId={condition_id[:16]}",
            }
            resp = await _relayer_post_authed(http, "/submit", body)
            tx_id = resp.get("transactionID")
            result["tx_id"] = tx_id
            log.info("redeem submitted tx_id=%s condition=%s tokens~=%.2f",
                     tx_id, condition_id[:16], estimated_tokens)

            if tx_id:
                mined = await _poll_transaction(http, tx_id, max_polls=40)
                if mined:
                    result["ok"] = True
                    result["tx_hash"] = mined.get("transactionHash")
                    log.info("redeem mined tx_hash=%s", result["tx_hash"])
                else:
                    result["error"] = "tx not mined within timeout"
            else:
                result["error"] = "no transactionID from relayer"

    except Exception as e:
        result["error"] = str(e)
        log.warning("redeem failed condition=%s: %s", condition_id[:16], e)

    return result
