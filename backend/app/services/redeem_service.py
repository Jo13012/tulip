"""
redeem_service.py — Redeem winning CTF tokens via Safe + Polymarket relayer.
"""
from __future__ import annotations

import logging

import httpx
from eth_abi import encode as abi_encode
from eth_utils import to_checksum_address
from eth_hash.auto import keccak

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

# All known collateral tokens — tried in order to find correct one
_COLLATERALS = [
    "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB",  # pUSD (V2)
    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",  # USDC.e (V1)
    "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2",  # WCOL (neg-risk)
]


def _get_position_id(collateral: str, condition_id_hex: str, index_set: int) -> int:
    """
    Compute ERC1155 tokenId = getPositionId(collateral, getCollectionId(0, conditionId, indexSet))
    getCollectionId(parentCollId, condId, indexSet) = keccak256(parentCollId ++ condId ++ indexSet)
    getPositionId(collateral, collectionId) = keccak256(collateral ++ collectionId) as uint256
    """
    cid = condition_id_hex.lower().lstrip("0x").zfill(64)
    condition_id_bytes = bytes.fromhex(cid)
    parent = bytes(32)
    index_set_bytes = index_set.to_bytes(32, "big")

    collection_id = keccak(parent + condition_id_bytes + index_set_bytes)

    collateral_bytes = bytes.fromhex(to_checksum_address(collateral)[2:].zfill(64))
    position_id = int.from_bytes(keccak(collateral_bytes + collection_id), "big")
    return position_id


def _detect_collateral(condition_id_hex: str, known_token_id: str | None) -> str:
    """
    Find the correct collateral for this condition by matching the known ERC1155 token_id
    from data-api against computed position IDs for each candidate collateral.
    Falls back to PUSD if no match found.
    """
    if not known_token_id:
        return PUSD

    try:
        known = int(known_token_id)
    except (ValueError, TypeError):
        try:
            known = int(known_token_id, 16)
        except Exception:
            return PUSD

    for collateral in _COLLATERALS:
        for index_set in (1, 2):
            computed = _get_position_id(collateral, condition_id_hex, index_set)
            if computed == known:
                log.info(
                    "redeem collateral detected: %s (indexSet=%d matches token_id=%s)",
                    collateral[:12], index_set, str(known_token_id)[:20],
                )
                return collateral

    log.warning(
        "redeem collateral NOT detected for conditionId=%s token_id=%s — falling back to pUSD",
        condition_id_hex[:20], str(known_token_id)[:20],
    )
    return PUSD


def _encode_redeem_positions(condition_id_hex: str, collateral: str) -> str:
    coll = to_checksum_address(collateral)
    parent_collection = bytes(32)

    cid = condition_id_hex.lower().lstrip("0x").zfill(64)
    condition_id_bytes = bytes.fromhex(cid)

    encoded = abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [coll, parent_collection, condition_id_bytes, [1, 2]],
    )
    calldata = "0x" + REDEEM_SELECTOR.hex() + encoded.hex()
    log.info("redeem encode: collateral=%s conditionId=%s", coll[:12], condition_id_hex[:20])
    return calldata


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
        "redeem START: eoa=%s safe=%s condition=%s token_id=%s estimated=%.4f",
        eoa_address[:10], safe_address[:10], condition_id, known_token_id, estimated_tokens,
    )
    try:
        collateral = _detect_collateral(condition_id, known_token_id)
        calldata = _encode_redeem_positions(condition_id, collateral)

        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as http:
            nonce = await _get_safe_nonce(http, safe_address)
            log.info("redeem nonce=%s safe=%s", nonce, safe_address[:10])

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
            log.info("redeem relayer response: %s", resp)

            tx_id = resp.get("transactionID")
            result["tx_id"] = tx_id

            if not tx_id:
                result["error"] = f"no transactionID from relayer: {resp}"
                return result

            log.info("redeem polling tx_id=%s", tx_id)
            mined = await _poll_transaction(http, tx_id, max_polls=40)
            if mined:
                result["ok"] = True
                result["tx_hash"] = mined.get("transactionHash")
                log.info("redeem MINED tx_hash=%s usdc~=%.2f", result["tx_hash"], estimated_tokens)
            else:
                result["error"] = "tx not mined within timeout (STATE_FAILED or timeout)"
                log.error("redeem FAILED tx_id=%s condition=%s", tx_id, condition_id[:20])

    except Exception as e:
        result["error"] = str(e)
        log.exception("redeem EXCEPTION condition=%s: %s", condition_id[:20], e)

    return result
