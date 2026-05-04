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


def _encode_redeem_positions(condition_id_hex: str) -> str:
    collateral = to_checksum_address(PUSD)
    parent_collection = bytes(32)

    cid = condition_id_hex.lower()
    if cid.startswith("0x"):
        cid = cid[2:]
    condition_id_bytes = bytes.fromhex(cid.zfill(64))

    encoded = abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [collateral, parent_collection, condition_id_bytes, [1, 2]],
    )
    calldata = "0x" + REDEEM_SELECTOR.hex() + encoded.hex()
    log.info("redeem encode: conditionId=%s calldata_prefix=%s", condition_id_hex[:20], calldata[:18])
    return calldata


async def redeem_positions(
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    condition_id: str,
    estimated_tokens: float = 0.0,
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
        "redeem START: eoa=%s safe=%s condition=%s estimated_tokens=%.4f",
        eoa_address[:10], safe_address[:10], condition_id[:20], estimated_tokens,
    )
    try:
        calldata = _encode_redeem_positions(condition_id)

        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as http:
            nonce = await _get_safe_nonce(http, safe_address)
            log.info("redeem nonce=%s safe=%s", nonce, safe_address[:10])

            sig = await _sign_safe_tx(
                privy_wallet_id, safe_address,
                CTF, calldata, "0", 0, nonce,
            )
            log.info("redeem sig=%s…", sig[:20])

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
            log.info("redeem submit body: from=%s to=%s proxyWallet=%s nonce=%s",
                     body["from"][:10], body["to"][:10], body["proxyWallet"][:10], nonce)

            resp = await _relayer_post_authed(http, "/submit", body)
            log.info("redeem relayer response: %s", resp)

            tx_id = resp.get("transactionID")
            result["tx_id"] = tx_id

            if not tx_id:
                result["error"] = f"no transactionID from relayer: {resp}"
                log.error("redeem no tx_id: %s", resp)
                return result

            log.info("redeem polling tx_id=%s condition=%s", tx_id, condition_id[:20])
            mined = await _poll_transaction(http, tx_id, max_polls=40)
            if mined:
                result["ok"] = True
                result["tx_hash"] = mined.get("transactionHash")
                log.info("redeem MINED tx_hash=%s condition=%s usdc~=%.2f",
                         result["tx_hash"], condition_id[:20], estimated_tokens)
            else:
                result["error"] = "tx not mined within timeout (STATE_FAILED or timeout)"
                log.error("redeem FAILED (not mined) tx_id=%s condition=%s", tx_id, condition_id[:20])

    except Exception as e:
        result["error"] = str(e)
        log.exception("redeem EXCEPTION condition=%s: %s", condition_id[:20], e)

    return result
