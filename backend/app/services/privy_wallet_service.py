"""
privy_wallet_service.py - Privy Server Wallet integration.

Wallet creation : Basic Auth (app_id:app_secret)
Signing         : Authorization Key (ECDSA P-256) - required for server-side signing

Implementation follows Privy's current request-signing flow:
  - Extract raw scalar from PKCS8 DER by finding [0x04, 0x20] marker
  - Canonicalize payload with RFC 8785 JCS (sort keys recursively)
  - Sign: ECDSA P-256 over SHA-256(canonical_bytes) -> DER -> base64 standard
  - Include privy-request-expiry in both headers and signed payload
"""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import httpx

from app.core.config import settings

PRIVY_API_BASE = "https://api.privy.io/v1"
log = logging.getLogger("uvicorn.error")


def _request_expiry() -> str:
    # Privy validates this header very strictly.
    # Use Unix time in milliseconds with a small buffer to avoid edge-case expiry.
    return str(int((time.time() + 300) * 1000))


def _basic_headers() -> dict:
    """Basic Auth — used for wallet creation/listing only."""
    creds = base64.b64encode(
        f"{settings.PRIVY_APP_ID}:{settings.PRIVY_APP_SECRET}".encode()
    ).decode()
    return {
        "Authorization": f"Basic {creds}",
        "privy-app-id": settings.PRIVY_APP_ID,
        "Content-Type": "application/json",
    }


def _extract_p256_scalar(b64_key: str) -> bytes:
    """
    Extract raw 32-byte private scalar from Privy authorization key.
    Privy key format: "wallet-auth:<base64_pkcs8_der>"
    SDK logic: find [0x04, 0x20] marker → next 32 bytes are the scalar.
    """
    raw = b64_key.strip()
    if raw.startswith("wallet-auth:"):
        raw = raw[len("wallet-auth:"):]
    der = base64.b64decode(raw)
    marker = bytes([0x04, 0x20])
    idx = der.find(marker)
    if idx == -1:
        raise ValueError("Invalid Privy authorization key: marker [0x04, 0x20] not found")
    return der[idx + 2: idx + 34]


def _jcs_canonicalize(obj) -> bytes:
    """
    RFC 8785 / JCS canonicalization.
    Matches the 'canonicalize' npm package used by the Privy SDK.
    Returns UTF-8 encoded bytes.
    """
    if isinstance(obj, dict):
        canonical = "{" + ",".join(
            f"{json.dumps(k, ensure_ascii=False, separators=(',', ':'))}:{_jcs_canonical_str(v)}"
            for k, v in sorted(obj.items())
        ) + "}"
        return canonical.encode("utf-8")
    return _jcs_canonical_str(obj).encode("utf-8")


def _jcs_canonical_str(obj) -> str:
    if isinstance(obj, dict):
        return "{" + ",".join(
            f"{json.dumps(k, ensure_ascii=False, separators=(',', ':'))}:{_jcs_canonical_str(v)}"
            for k, v in sorted(obj.items())
        ) + "}"
    if isinstance(obj, list):
        return "[" + ",".join(_jcs_canonical_str(v) for v in obj) + "]"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if obj is None:
        return "null"
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, float):
        # JCS uses standard JS number serialization
        return json.dumps(obj)
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    raise TypeError(f"Cannot canonicalize type: {type(obj)}")


def _sign_authorization(method: str, url: str, body: Any, request_expiry: str) -> str:
    """
    Build the privy-authorization-signature header value.
    Follows @privy-io/server-auth SDK exactly.
    Returns base64-encoded DER signature.
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    # Build payload object (exact structure from SDK)
    payload_obj = {
        "version": 1,
        "method": method.upper(),
        "url": url.rstrip("/"),
        "body": body,
        "headers": {
            "privy-app-id": settings.PRIVY_APP_ID,
            "privy-request-expiry": request_expiry,
        },
    }

    # Canonicalize → bytes
    payload_bytes = _jcs_canonicalize(payload_obj)

    # Extract scalar from Privy key
    scalar_bytes = _extract_p256_scalar(settings.PRIVY_AUTHORIZATION_KEY)

    # Load private key from raw scalar
    private_value = int.from_bytes(scalar_bytes, "big")
    private_key = ec.derive_private_key(private_value, ec.SECP256R1(), default_backend())

    # Sign: ECDSA P-256, SHA-256 (cryptography library hashes internally)
    from cryptography.hazmat.primitives import hashes
    der_sig = private_key.sign(payload_bytes, ec.ECDSA(hashes.SHA256()))

    return base64.b64encode(der_sig).decode()


def _authorization_headers(method: str, url: str, body_parsed: Any) -> dict:
    request_expiry = _request_expiry()
    sig = _sign_authorization(method, url, body_parsed, request_expiry)
    headers = {
        "privy-authorization-signature": sig,
        "privy-request-expiry": request_expiry,
    }
    if settings.PRIVY_AUTHORIZATION_KEY_ID:
        headers["privy-authorization-key-id"] = settings.PRIVY_AUTHORIZATION_KEY_ID
    return headers


def validate_authorization_config() -> tuple[bool, str]:
    if not settings.PRIVY_AUTHORIZATION_KEY_ID:
        return False, "PRIVY_AUTHORIZATION_KEY_ID is missing"
    if not settings.PRIVY_AUTHORIZATION_KEY:
        return False, "PRIVY_AUTHORIZATION_KEY is missing"
    try:
        scalar = _extract_p256_scalar(settings.PRIVY_AUTHORIZATION_KEY)
        if len(scalar) != 32:
            return False, "PRIVY_AUTHORIZATION_KEY could not be parsed into a P-256 scalar"
    except Exception as exc:
        return False, f"PRIVY_AUTHORIZATION_KEY is invalid: {exc}"
    return True, f"Privy authorization key loaded for signer id {settings.PRIVY_AUTHORIZATION_KEY_ID}"


class PrivyWalletError(Exception):
    pass


class PrivyWalletService:

    def __init__(self):
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if not self._http or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15.0, trust_env=False)
        return self._http

    async def close(self):
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ── Wallet creation ───────────────────────────────────────────────────────

    async def get_or_create_wallet(self, privy_user_id: str) -> dict:
        existing = await self._list_user_wallets(privy_user_id)
        if existing:
            return {
                "privy_wallet_id": existing[0]["id"],
                "wallet_address": existing[0]["address"],
            }

        resp = await self._client().post(
            f"{PRIVY_API_BASE}/wallets",
            headers=_basic_headers(),
            json={
                "chain_type": "ethereum",
                "owner": {"user_id": privy_user_id},
                "additional_signers": [{"signer_id": settings.PRIVY_AUTHORIZATION_KEY_ID}],
            },
        )
        if not resp.is_success:
            raise PrivyWalletError(
                f"Failed to create Privy wallet: {resp.status_code} {resp.text}"
            )
        data = resp.json()
        return {
            "privy_wallet_id": data["id"],
            "wallet_address": data["address"],
        }

    async def _list_user_wallets(self, privy_user_id: str) -> list[dict]:
        resp = await self._client().get(
            f"{PRIVY_API_BASE}/wallets",
            headers=_basic_headers(),
            params={"owner_id": privy_user_id, "chain_type": "ethereum"},
        )
        if not resp.is_success:
            return []
        return resp.json().get("data", [])

    # ── Signing (uses authorization key) ─────────────────────────────────────

    async def sign_typed_data(self, privy_wallet_id: str, typed_data: dict[str, Any]) -> str:
        url = f"{PRIVY_API_BASE}/wallets/{privy_wallet_id}/rpc"
        # Privy API uses snake_case: primary_type not primaryType
        td = dict(typed_data)
        if "primaryType" in td:
            td["primary_type"] = td.pop("primaryType")
        body = {
            "method": "eth_signTypedData_v4",
            "params": {"typed_data": td},
        }
        body_str = json.dumps(body, separators=(",", ":"))
        headers = {
            **_basic_headers(),
            **_authorization_headers("POST", url, body),
        }
        resp = await self._client().post(url, headers=headers, content=body_str)
        if not resp.is_success:
            raise PrivyWalletError(
                f"eth_signTypedData_v4 failed: {resp.status_code} {resp.text}"
            )
        return resp.json()["data"]["signature"]

    async def personal_sign(self, privy_wallet_id: str, message: str, encoding: str = "utf-8") -> str:
        url = f"{PRIVY_API_BASE}/wallets/{privy_wallet_id}/rpc"
        body = {
            "method": "personal_sign",
            "params": {"message": message, "encoding": encoding},
        }
        body_str = json.dumps(body, separators=(",", ":"))
        headers = {
            **_basic_headers(),
            **_authorization_headers("POST", url, body),
        }
        resp = await self._client().post(url, headers=headers, content=body_str)
        if not resp.is_success:
            raise PrivyWalletError(
                f"personal_sign failed: {resp.status_code} {resp.text}"
            )
        return resp.json()["data"]["signature"]

    async def secp256k1_sign(self, privy_wallet_id: str, hash_hex: str) -> str:
        """
        Sign a raw 32-byte hash directly (no Ethereum prefix added by Privy).
        hash_hex: 0x-prefixed 32-byte hex string.
        Returns 65-byte hex sig with v=0 or v=1.
        """
        url = f"{PRIVY_API_BASE}/wallets/{privy_wallet_id}/rpc"
        body = {
            "method": "secp256k1_sign",
            "params": {"hash": hash_hex},
        }
        body_str = json.dumps(body, separators=(",", ":"))
        headers = {
            **_basic_headers(),
            **_authorization_headers("POST", url, body),
        }
        resp = await self._client().post(url, headers=headers, content=body_str)
        if not resp.is_success:
            raise PrivyWalletError(
                f"secp256k1_sign failed: {resp.status_code} {resp.text}"
            )
        return resp.json()["data"]["signature"]

    async def get_wallet_address(self, privy_wallet_id: str) -> str:
        resp = await self._client().get(
            f"{PRIVY_API_BASE}/wallets/{privy_wallet_id}",
            headers=_basic_headers(),
        )
        if not resp.is_success:
            raise PrivyWalletError(f"get wallet failed: {resp.status_code} {resp.text}")
        return resp.json()["address"]


privy_wallet_service = PrivyWalletService()
