"""
polymarket_onboarding_service.py — Automatic Polymarket onboarding for new users.

Flow (runs once per wallet at first login):
  1. Derive Safe address deterministically from EOA (CREATE2, no network call)
  2. Check if Safe already deployed via relayer
  3. If not: deploy Safe via relayer (uses platform builder creds)
  4. Derive or create CLOB API credentials (signed by Privy server wallet)
  5. Execute the 6 token approvals (pUSD + ERC1155 × 3 spenders) via Safe
  6. Store proxy_address + clob creds in DB, set onboarding_state = ready

Contract addresses (Polygon mainnet, Polymarket V2 — live from 2026-04-28):
  pUSD:                0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB  (replaces USDC.e)
  CTF:                 0x4d97dcd97ec945f40cf65f87097ace5ea0476045
  CTF Exchange V2:     0xE111180000d2663C0091e4f400237545B87B996B
  Neg Risk CTF Exch V2:0xe2222d279d744050d28e00520010520000310F59
  Neg Risk Adapter:    0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from base64 import b64decode, b64encode
from typing import Any, Awaitable, Callable

import httpx
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address

from app.core.config import settings
from app.services.privy_wallet_service import privy_wallet_service

log = logging.getLogger("onboarding")

# ── Constants ──────────────────────────────────────────────────────────────────

RELAYER_URL = settings.POLYMARKET_RELAYER_URL
CLOB_URL = "https://clob-proxy.jolpmaker.workers.dev"
CHAIN_ID = 137

SAFE_FACTORY = "0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b"
SAFE_MULTISEND = "0xA238CBeb142c10Ef7Ad8442C6D1f9E89e07e7761"
SAFE_INIT_CODE_HASH = "0x2bce2127ff07fb632d16c8347c4ebf501f4841168bed00d9e6ef715ddb6fcecf"
SAFE_FACTORY_NAME = "Polymarket Contract Proxy Factory"

PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # V2 collateral token (replaces USDC.e)
CTF  = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
CTF_EXCHANGE        = "0xE111180000d2663C0091e4f400237545B87B996B"  # V2
NEG_RISK_CTF_EXCH   = "0xe2222d279d744050d28e00520010520000310F59"  # V2
NEG_RISK_ADAPTER    = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

MAX_UINT256 = 2**256 - 1
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# ERC-20 approve(address,uint256) selector
APPROVE_SELECTOR = bytes.fromhex("095ea7b3")
# ERC-1155 setApprovalForAll(address,bool) selector
SET_APPROVAL_FOR_ALL_SELECTOR = bytes.fromhex("a22cb465")

# Spenders that need ERC-20 (pUSD) approval
ERC20_SPENDERS = [CTF_EXCHANGE, NEG_RISK_CTF_EXCH, NEG_RISK_ADAPTER]
# Spenders that need ERC-1155 (CTF token) approval
ERC1155_SPENDERS = [CTF_EXCHANGE, NEG_RISK_CTF_EXCH, NEG_RISK_ADAPTER]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _derive_safe(eoa_address: str) -> str:
    """
    Deterministic Safe address from EOA using CREATE2.
    Python port of deriveSafe() from @polymarket/builder-relayer-client.
    """
    addr_clean = eoa_address.lower()
    if not addr_clean.startswith("0x"):
        addr_clean = "0x" + addr_clean

    # salt = keccak256(abi.encode(address))
    salt = keccak(abi_encode(["address"], [to_checksum_address(addr_clean)]))

    # CREATE2: keccak256(0xff ++ factory ++ salt ++ initCodeHash)
    factory_bytes = bytes.fromhex(SAFE_FACTORY[2:])
    init_code_hash = bytes.fromhex(SAFE_INIT_CODE_HASH[2:])
    data = b"\xff" + factory_bytes + salt + init_code_hash
    h = keccak(data)
    # last 20 bytes = address
    safe_addr = "0x" + h[12:].hex()
    return to_checksum_address(safe_addr)


def _builder_headers(method: str, path: str, body: str | None = None) -> dict:
    """HMAC-SHA256 builder authentication headers."""
    if not settings.POLYMARKET_BUILDER_API_KEY:
        raise RuntimeError("POLYMARKET_BUILDER_API_KEY is missing")
    if not settings.POLYMARKET_BUILDER_SECRET:
        raise RuntimeError("POLYMARKET_BUILDER_SECRET is missing")
    if not settings.POLYMARKET_BUILDER_PASSPHRASE:
        raise RuntimeError("POLYMARKET_BUILDER_PASSPHRASE is missing")

    ts = str(int(time.time()))
    secret = settings.POLYMARKET_BUILDER_SECRET
    message = ts + method.upper() + path
    if body:
        message += body
    base64_secret = b64decode(secret.replace("-", "+").replace("_", "/"))
    sig = hmac.new(base64_secret, message.encode(), hashlib.sha256).digest()
    # URL-safe base64, keep = suffix
    sig_b64 = b64encode(sig).decode()
    sig_url_safe = sig_b64.replace("+", "-").replace("/", "_")
    return {
        "POLY_BUILDER_API_KEY": settings.POLYMARKET_BUILDER_API_KEY,
        "POLY_BUILDER_PASSPHRASE": settings.POLYMARKET_BUILDER_PASSPHRASE,
        "POLY_BUILDER_SIGNATURE": sig_url_safe,
        "POLY_BUILDER_TIMESTAMP": ts,
        "Content-Type": "application/json",
    }


def _erc20_approve_data(spender: str) -> str:
    """Encode approve(spender, MAX_UINT256) calldata."""
    spender_padded = bytes.fromhex(spender[2:].lower().zfill(64))
    amount_padded = MAX_UINT256.to_bytes(32, "big")
    return "0x" + APPROVE_SELECTOR.hex() + spender_padded.hex() + amount_padded.hex()


def _erc1155_approve_data(spender: str) -> str:
    """Encode setApprovalForAll(spender, true) calldata."""
    spender_padded = bytes.fromhex(spender[2:].lower().zfill(64))
    true_padded = (1).to_bytes(32, "big")
    return "0x" + SET_APPROVAL_FOR_ALL_SELECTOR.hex() + spender_padded.hex() + true_padded.hex()


def _build_approval_txns() -> list[dict]:
    """Build the 6 approval transactions (3 ERC20 + 3 ERC1155)."""
    txns = []
    for spender in ERC20_SPENDERS:
        txns.append({"to": PUSD, "data": _erc20_approve_data(spender), "value": "0", "operation": 0})
    for spender in ERC1155_SPENDERS:
        txns.append({"to": CTF, "data": _erc1155_approve_data(spender), "value": "0", "operation": 0})
    return txns


def _encode_multisend(txns: list[dict]) -> str:
    """Encode multiple transactions into Safe multisend calldata."""
    # multiSend(bytes) selector
    MULTISEND_SELECTOR = bytes.fromhex("8d80ff0a")
    packed = b""
    for txn in txns:
        operation = txn.get("operation", 0)
        to = bytes.fromhex(txn["to"][2:].lower())
        value = int(txn.get("value", "0")).to_bytes(32, "big")
        data = bytes.fromhex(txn["data"][2:]) if txn["data"].startswith("0x") else bytes.fromhex(txn["data"])
        data_length = len(data).to_bytes(32, "big")
        packed += operation.to_bytes(1, "big") + to + value + data_length + data

    # abi encode bytes
    inner_data = (
        MULTISEND_SELECTOR
        + (32).to_bytes(32, "big")         # offset
        + len(packed).to_bytes(32, "big")  # length
        + packed
        + (b"\x00" * ((32 - len(packed) % 32) % 32))  # padding
    )
    return "0x" + inner_data.hex()


async def _relayer_get(http: httpx.AsyncClient, path: str, params: dict | None = None) -> Any:
    resp = await http.get(f"{RELAYER_URL}{path}", params=params, timeout=15.0)
    resp.raise_for_status()
    return resp.json()


async def _relayer_post_authed(http: httpx.AsyncClient, path: str, body: dict) -> Any:
    body_str = json.dumps(body)
    headers = _builder_headers("POST", path, body_str)
    resp = await http.post(f"{RELAYER_URL}{path}", content=body_str, headers=headers, timeout=30.0)
    if not resp.is_success:
        log.error("Relayer %s %s -> %s: %s", "POST", path, resp.status_code, resp.text[:800])
        if resp.status_code == 401:
            raise RuntimeError(
                "Polymarket relayer authorization failed. Check POLYMARKET_BUILDER_API_KEY, "
                "POLYMARKET_BUILDER_SECRET, and POLYMARKET_BUILDER_PASSPHRASE."
            )
    resp.raise_for_status()
    return resp.json()


async def _poll_transaction(http: httpx.AsyncClient, tx_id: str, max_polls: int = 60) -> dict | None:
    for _ in range(max_polls):
        data = await _relayer_get(http, "/transaction", {"id": tx_id})
        if isinstance(data, list) and data:
            txn = data[0]
            state = txn.get("state", "")
            if state in ("mined", "confirmed", "STATE_MINED", "STATE_CONFIRMED", "STATE_EXECUTED"):
                return txn
            if state in ("failed", "STATE_FAILED", "STATE_INVALID"):
                log.error("relayer transaction %s failed: %s", tx_id, state)
                return None
        await _async_sleep(3)
    log.error("relayer transaction %s timed out", tx_id)
    return None


async def _async_sleep(seconds: float):
    import asyncio
    await asyncio.sleep(seconds)


# ── CLOB API creds (L2 auth) ──────────────────────────────────────────────────

async def _derive_or_create_clob_creds(
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    http: httpx.AsyncClient,
) -> dict:
    """
    Derive (or create) Polymarket CLOB API credentials.
    POLY_ADDRESS = EOA (per official docs). EOA signs the EIP-712 ClobAuth.
    """
    timestamp = str(int(time.time()))
    nonce = 0

    typed_data = {
        "domain": {
            "name": "ClobAuthDomain",
            "version": "1",
            "chainId": CHAIN_ID,
        },
        "types": {
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ]
        },
        "primary_type": "ClobAuth",
        "message": {
            "address": eoa_address,
            "timestamp": timestamp,
            "nonce": nonce,
            "message": "This message attests that I control the given wallet",
        },
    }
    sig = await privy_wallet_service.sign_typed_data(privy_wallet_id, typed_data)

    headers = {
        "POLY_ADDRESS": eoa_address,
        "POLY_SIGNATURE": sig,
        "POLY_TIMESTAMP": timestamp,
        "POLY_NONCE": str(nonce),
        "Content-Type": "application/json",
    }

    # Try derive first (returning user), then create — retry loop for CLOB indexing lag
    last_err: str = ""
    for attempt in range(6):
        if attempt > 0:
            wait = 5 * attempt
            log.info("CLOB creds attempt %d/6 — waiting %ds (CLOB indexing lag)...", attempt + 1, wait)
            await _async_sleep(wait)
            # Refresh signature timestamp on each retry
            timestamp = str(int(time.time()))
            typed_data["message"]["timestamp"] = timestamp
            sig = await privy_wallet_service.sign_typed_data(privy_wallet_id, typed_data)
            headers["POLY_SIGNATURE"] = sig
            headers["POLY_TIMESTAMP"] = timestamp

        derive_ok = False
        try:
            resp = await http.get(f"{CLOB_URL}/auth/derive-api-key", headers=headers, timeout=15.0)
            log.info("derive-api-key → %d %s", resp.status_code, resp.text[:200])
            derive_ok = True
            if resp.is_success:
                data = resp.json()
                if data.get("apiKey") and data.get("secret") and data.get("passphrase"):
                    log.info("Derived existing CLOB creds for %s", safe_address[:10])
                    return {"key": data["apiKey"], "secret": data["secret"], "passphrase": data["passphrase"]}
        except Exception as e:
            log.warning("derive-api-key exception: %s — will retry", e)
            last_err = str(e)
            continue  # don't attempt create if derive errored (proxy/network issue)

        # Only attempt create after a successful derive call (Safe indexed by CLOB)
        if not derive_ok:
            continue

        try:
            resp = await http.post(f"{CLOB_URL}/auth/api-key", headers=headers, timeout=15.0)
            log.info("create api-key → %d %s", resp.status_code, resp.text[:200])
            if resp.is_success:
                data = resp.json()
                log.info("Created new CLOB creds for %s", safe_address[:10])
                return {"key": data["apiKey"], "secret": data["secret"], "passphrase": data["passphrase"]}
            last_err = f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception as e:
            last_err = str(e)
            log.warning("create api-key exception: %s", e)

    raise RuntimeError(f"Failed to create CLOB API key after 6 attempts: {last_err}")


# ── Safe deployment + approvals ────────────────────────────────────────────────

async def _get_safe_nonce(http: httpx.AsyncClient, safe_address: str) -> str:
    data = await _relayer_get(http, "/nonce", {"address": safe_address, "type": "SAFE"})
    return str(data["nonce"])


async def _sign_safe_create(
    privy_wallet_id: str,
    eoa_address: str,
) -> str:
    """Sign the SafeCreate typed data via Privy."""
    typed_data = {
        "domain": {
            "name": SAFE_FACTORY_NAME,
            "chainId": CHAIN_ID,
            "verifyingContract": SAFE_FACTORY,
        },
        "types": {
            "CreateProxy": [
                {"name": "paymentToken", "type": "address"},
                {"name": "payment", "type": "uint256"},
                {"name": "paymentReceiver", "type": "address"},
            ]
        },
        "primary_type": "CreateProxy",
        "message": {
            "paymentToken": ZERO_ADDRESS,
            "payment": 0,
            "paymentReceiver": ZERO_ADDRESS,
        },
    }
    return await privy_wallet_service.sign_typed_data(privy_wallet_id, typed_data)


def _hash_safe_tx(
    safe_address: str,
    to: str,
    data: str,
    value: str,
    operation: int,
    nonce: str,
) -> bytes:
    """
    Compute EIP-712 final hash for a Safe transaction.
    Matches viem hashTypedData used by @polymarket/builder-relayer-client.
    """
    from eth_abi import encode as abi_encode

    SAFE_TX_TYPEHASH = keccak(
        b"SafeTx(address to,uint256 value,bytes data,uint8 operation,"
        b"uint256 safeTxGas,uint256 baseGas,uint256 gasPrice,address gasToken,"
        b"address refundReceiver,uint256 nonce)"
    )
    data_bytes = bytes.fromhex(data[2:]) if data.startswith("0x") else bytes.fromhex(data)
    data_hash = keccak(data_bytes)

    struct_hash = keccak(
        SAFE_TX_TYPEHASH
        + abi_encode(["address"], [to_checksum_address(to)])
        + abi_encode(["uint256"], [int(value)])
        + data_hash
        + abi_encode(["uint8"], [operation])
        + abi_encode(["uint256"], [0])   # safeTxGas
        + abi_encode(["uint256"], [0])   # baseGas
        + abi_encode(["uint256"], [0])   # gasPrice
        + abi_encode(["address"], [to_checksum_address(ZERO_ADDRESS)])
        + abi_encode(["address"], [to_checksum_address(ZERO_ADDRESS)])
        + abi_encode(["uint256"], [int(nonce)])
    )

    domain_separator = keccak(
        keccak(b"EIP712Domain(uint256 chainId,address verifyingContract)")
        + abi_encode(["uint256"], [CHAIN_ID])
        + abi_encode(["address"], [to_checksum_address(safe_address)])
    )

    return keccak(b"\x19\x01" + domain_separator + struct_hash)


async def _sign_safe_tx(
    privy_wallet_id: str,
    safe_address: str,
    to: str,
    data: str,
    value: str,
    operation: int,
    nonce: str,
) -> str:
    """
    Sign Safe tx using secp256k1_sign on the Ethereum-prefixed hash.

    Flow (matches @polymarket/builder-relayer-client / ethers EthersSigner.signMessage):
      1. Compute EIP-712 SafeTx hash (dataHash)
      2. Compute prefixedHash = keccak256("\\x19Ethereum Signed Message:\\n32" + dataHash)
      3. secp256k1_sign(prefixedHash) — Privy signs raw hash with no prefix
      4. Pack v: 27/28 → 31/32 (Safe eth_sign verification path)

    Safe.checkNSignatures for v=31/32:
      ecrecover(keccak256("\\x19Ethereum Signed Message:\\n32" + dataHash), v-4, r, s) → EOA ✓
    """
    safe_hash = _hash_safe_tx(safe_address, to, data, value, operation, nonce)
    prefixed_hash = keccak(b"\x19Ethereum Signed Message:\n32" + safe_hash)
    raw_sig = await privy_wallet_service.secp256k1_sign(privy_wallet_id, "0x" + prefixed_hash.hex())
    return _pack_gnosis_sig(raw_sig)


def _pack_gnosis_sig(sig: str) -> str:
    """
    Pack ECDSA sig into Gnosis eth_sign format.
    v: 27/28 → 31/32, 0/1 → 31/32 (matches splitAndPackSig in relayer SDK).
    """
    sig_bytes = bytes.fromhex(sig[2:] if sig.startswith("0x") else sig)
    r = sig_bytes[:32]
    s = sig_bytes[32:64]
    v = sig_bytes[64]
    if v in (0, 1):
        v += 31
    elif v in (27, 28):
        v += 4
    return "0x" + r.hex() + s.hex() + v.to_bytes(1, "big").hex()


# ── Main onboarding function ───────────────────────────────────────────────────

async def onboard_wallet(
    privy_wallet_id: str,
    eoa_address: str,
    on_stage: Callable[[str], Awaitable[None] | None] | None = None,
    pre_signed_clob_creds: dict | None = None,
) -> dict:
    """
    Full onboarding flow. Returns:
      {
        "proxy_address": str,       # Safe address
        "clob_api_key": str,
        "clob_api_secret": str,
        "clob_api_passphrase": str,
      }
    """
    async def emit_stage(stage: str) -> None:
        if not on_stage:
            return
        result = on_stage(stage)
        if result is not None:
            await result

    async with httpx.AsyncClient(timeout=30.0, trust_env=False, headers=_CLOB_HEADERS) as http:
        safe_address = _derive_safe(eoa_address)
        log.info("Derived Safe address %s for EOA %s", safe_address, eoa_address[:10])

        # ── 1. Deploy Safe if needed ──────────────────────────────────────────
        await emit_stage("deploying")
        deployed_resp = await _relayer_get(http, "/deployed", {"address": safe_address})
        is_deployed = deployed_resp.get("deployed", False)

        if not is_deployed:
            log.info("Deploying Safe %s...", safe_address)
            sig = await _sign_safe_create(privy_wallet_id, eoa_address)
            deploy_body = {
                "from": eoa_address,
                "to": SAFE_FACTORY,
                "proxyWallet": safe_address,
                "data": "0x",
                "signature": sig,
                "signatureParams": {
                    "paymentToken": ZERO_ADDRESS,
                    "payment": "0",
                    "paymentReceiver": ZERO_ADDRESS,
                },
                "type": "SAFE-CREATE",
            }
            log.info("Deploy body: %s", json.dumps(deploy_body))
            deploy_resp = await _relayer_post_authed(http, "/submit", deploy_body)
            tx_id = deploy_resp.get("transactionID")
            log.info("Safe deploy submitted tx_id=%s", tx_id)
            result = await _poll_transaction(http, tx_id, max_polls=30)
            if not result:
                raise RuntimeError(f"Safe deployment failed for {safe_address}")
            log.info("Safe deployed: %s", safe_address)
        else:
            log.info("Safe already deployed: %s", safe_address)

        # ── 2. CLOB API credentials ───────────────────────────────────────────
        await emit_stage("approving")
        if pre_signed_clob_creds and pre_signed_clob_creds.get("key") and pre_signed_clob_creds.get("secret") and pre_signed_clob_creds.get("passphrase"):
            creds = pre_signed_clob_creds
            log.info("Using pre-signed CLOB creds from frontend for %s", safe_address[:10])
        else:
            creds = await _derive_or_create_clob_creds(privy_wallet_id, eoa_address, safe_address, http)

        # ── 3. Token approvals ────────────────────────────────────────────────
        # Check if approvals already done by checking USDC allowance to CTF_EXCHANGE
        # (skip check for now — approvals are idempotent, just execute)
        log.info("Setting token approvals for Safe %s...", safe_address)
        txns = _build_approval_txns()

        if len(txns) == 1:
            agg = txns[0]
            operation = agg["operation"]
        else:
            # Multisend
            multisend_data = _encode_multisend(txns)
            agg = {"to": SAFE_MULTISEND, "data": multisend_data, "value": "0", "operation": 1}
            operation = 1

        nonce = await _get_safe_nonce(http, safe_address)
        sig = await _sign_safe_tx(
            privy_wallet_id, safe_address,
            agg["to"], agg["data"], agg["value"], operation, nonce,
        )

        approve_body = {
            "from": eoa_address,
            "to": agg["to"],
            "proxyWallet": safe_address,
            "data": agg["data"],
            "nonce": nonce,
            "signature": sig,
            "signatureParams": {
                "gasPrice": "0",
                "operation": str(operation),
                "safeTxnGas": "0",
                "baseGas": "0",
                "gasToken": ZERO_ADDRESS,
                "refundReceiver": ZERO_ADDRESS,
            },
            "type": "SAFE",
            "metadata": "set all token approvals",
        }
        approve_resp = await _relayer_post_authed(http, "/submit", approve_body)
        tx_id = approve_resp.get("transactionID")
        log.info("Approvals submitted tx_id=%s", tx_id)
        result = await _poll_transaction(http, tx_id, max_polls=30)
        if not result:
            raise RuntimeError(f"Token approvals failed for {safe_address}")
        log.info("Approvals done for %s", safe_address)
        await emit_stage("finalizing")

        return {
            "proxy_address": safe_address,
            "clob_api_key": creds["key"],
            "clob_api_secret": creds["secret"],
            "clob_api_passphrase": creds["passphrase"],
        }


_CLOB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://polymarket.com",
    "Referer": "https://polymarket.com/",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

polymarket_onboarding_service = None  # singleton placeholder — just import and call onboard_wallet()
