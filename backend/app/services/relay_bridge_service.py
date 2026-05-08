"""
relay_bridge_service.py — Bridge $1 USDC (Arbitrum) → POL (Polygon) via relay.link.
Used to fund EOA with POL for neg-risk redeem gas.
"""
from __future__ import annotations

import logging
import httpx

from app.services.privy_tx_service import _privy_rpc, PrivyTxError

log = logging.getLogger("relay_bridge")

_RELAY_QUOTE_URL = "https://api.relay.link/quote/v2"

USDC_ARB   = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
POL_POLYGON = "0x0000000000000000000000000000000000000000"
ARB_CHAIN_ID  = 42161
POLY_CHAIN_ID = 137

USDC_AMOUNT = "1000000"  # $1


async def bridge_pol_for_gas(
    privy_wallet_id: str,
    eoa_address: str,
) -> dict:
    """
    Bridge $1 USDC from Arbitrum → POL on Polygon for eoa_address.
    Returns {"ok": True, "tx_hash": "0x..."} or {"ok": False, "error": "..."}.
    """
    result: dict = {"ok": False, "tx_hash": None, "error": None}

    try:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as http:
            # 1. Get quote
            quote_body = {
                "user": eoa_address,
                "recipient": eoa_address,
                "originChainId": ARB_CHAIN_ID,
                "destinationChainId": POLY_CHAIN_ID,
                "originCurrency": USDC_ARB,
                "destinationCurrency": POL_POLYGON,
                "amount": USDC_AMOUNT,
                "tradeType": "EXACT_INPUT",
            }
            resp = await http.post(_RELAY_QUOTE_URL, json=quote_body)
            if not resp.is_success:
                result["error"] = f"relay.link quote error {resp.status_code}: {resp.text[:200]}"
                log.error("bridge_pol: %s", result["error"])
                return result

            quote = resp.json()
            steps = quote.get("steps") or []
            if not steps:
                result["error"] = f"relay.link no steps in response: {str(quote)[:200]}"
                log.error("bridge_pol: %s", result["error"])
                return result

            # Extract first step transaction
            items = steps[0].get("items") or []
            if not items:
                result["error"] = "relay.link step[0] has no items"
                log.error("bridge_pol: %s", result["error"])
                return result

            tx_data = items[0].get("data") or {}
            to    = tx_data.get("to")
            data  = tx_data.get("data") or "0x"
            value = tx_data.get("value") or "0x0"
            if not to:
                result["error"] = f"relay.link missing tx.to: {str(tx_data)[:200]}"
                log.error("bridge_pol: %s", result["error"])
                return result

            log.info("bridge_pol: quote ok, submitting arb tx to=%s value=%s", to[:12], value)

            # 2. Submit via Privy on Arbitrum
            try:
                rpc_result = await _privy_rpc(privy_wallet_id, {
                    "method": "eth_sendTransaction",
                    "caip2": f"eip155:{ARB_CHAIN_ID}",
                    "params": {
                        "transaction": {
                            "from": eoa_address,
                            "to": to,
                            "data": data,
                            "value": value,
                        }
                    },
                })
            except PrivyTxError as e:
                result["error"] = f"privy tx error: {e}"
                log.error("bridge_pol: %s", result["error"])
                return result

            data_out = rpc_result.get("data") or {}
            tx_hash = (
                data_out.get("hash")
                or data_out.get("txHash")
                or rpc_result.get("result")
                or rpc_result.get("hash")
            )
            if not tx_hash:
                result["error"] = f"no tx_hash in response: {str(rpc_result)[:200]}"
                log.error("bridge_pol: %s", result["error"])
                return result

            log.info("bridge_pol: submitted tx=%s eoa=%s", tx_hash, eoa_address[:12])
            result["ok"] = True
            result["tx_hash"] = tx_hash

    except Exception as e:
        result["error"] = str(e)
        log.exception("bridge_pol: exception eoa=%s", eoa_address[:12])

    return result
