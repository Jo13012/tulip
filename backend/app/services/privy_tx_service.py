"""
privy_tx_service.py - Send on-chain txs via Privy Server Wallet RPC.
"""
from __future__ import annotations

import base64
import logging
from decimal import Decimal

import httpx
import json

from app.core.config import settings
from app.services.privy_wallet_service import _authorization_headers

log = logging.getLogger("uvicorn.error")

PRIVY_API_BASE = "https://api.privy.io/v1"


class PrivyTxError(Exception):
    pass


def _auth_header() -> str:
    creds = f"{settings.PRIVY_APP_ID}:{settings.PRIVY_APP_SECRET}".encode()
    return "Basic " + base64.b64encode(creds).decode()


async def _privy_rpc(privy_wallet_id: str, payload: dict) -> dict:
    if not settings.PRIVY_APP_ID or not settings.PRIVY_APP_SECRET:
        raise PrivyTxError("Privy app credentials missing")
    url = f"{PRIVY_API_BASE}/wallets/{privy_wallet_id}/rpc"
    body_str = json.dumps(payload, separators=(",", ":"))
    headers = {
        "Authorization": _auth_header(),
        "privy-app-id": settings.PRIVY_APP_ID,
        "Content-Type": "application/json",
        **_authorization_headers("POST", url, payload),
        **({"privy-authorization-key-id": settings.PRIVY_AUTHORIZATION_KEY_ID} if settings.PRIVY_AUTHORIZATION_KEY_ID else {}),
    }
    async with httpx.AsyncClient(timeout=20.0, trust_env=False) as http:
        resp = await http.post(url, headers=headers, content=body_str)
        if not resp.is_success:
            if resp.status_code == 401:
                raise PrivyTxError(
                    "Privy authorization failed. The server authorization key or key id is invalid, expired, "
                    "or no longer attached to this wallet."
                )
            raise PrivyTxError(f"Privy RPC error {resp.status_code}: {resp.text}")
        return resp.json()


def _encode_erc20_transfer(to_address: str, amount: int) -> str:
    # transfer(address,uint256) selector = a9059cbb
    selector = "a9059cbb"
    to_clean = to_address.lower().replace("0x", "").rjust(64, "0")
    amt_hex = hex(amount)[2:].rjust(64, "0")
    return "0x" + selector + to_clean + amt_hex


async def transfer_usdc(
    *,
    privy_wallet_id: str,
    wallet_address: str,
    to_address: str,
    amount_usdc: float,
) -> str:
    usdc_contract = settings.ARBITRUM_USDC_ADDRESS
    if not usdc_contract:
        raise PrivyTxError("USDC contract not configured")
    if amount_usdc <= 0:
        raise PrivyTxError("Amount must be > 0")

    amount_base = int(Decimal(str(amount_usdc)) * Decimal("1000000"))
    data = _encode_erc20_transfer(to_address, amount_base)

    payload = {
        "method": "eth_sendTransaction",
        "caip2": f"eip155:{settings.ARBITRUM_CHAIN_ID}",
        "params": {
            "transaction": {
                "from": wallet_address,
                "to": usdc_contract,
                "data": data,
                "value": "0x0",
            }
        },
    }
    result = await _privy_rpc(privy_wallet_id, payload)
    data = result.get("data") or {}
    tx_hash = (
        data.get("hash")
        or data.get("txHash")
        or data.get("tx_hash")
        or result.get("result")
        or result.get("hash")
    )
    if not tx_hash:
        raise PrivyTxError("Privy RPC did not return a tx hash")
    return tx_hash
