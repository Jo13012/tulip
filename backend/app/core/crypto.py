"""
crypto.py — Wallet private key encryption/decryption.

Uses Fernet (AES-128-CBC + HMAC-SHA256) symmetric encryption.
Key stored in WALLET_ENCRYPTION_KEY env var (32-byte base64url string).

Generate a key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Optional[Fernet]:
    key = settings.WALLET_ENCRYPTION_KEY
    if not key:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None


def encrypt_private_key(raw_key: str) -> str:
    """
    Encrypt a hex private key for storage in DB.
    Returns base64url ciphertext string.
    Raises RuntimeError if WALLET_ENCRYPTION_KEY is not set.
    """
    f = _fernet()
    if not f:
        raise RuntimeError(
            "WALLET_ENCRYPTION_KEY not set — cannot store private keys safely. "
            "Generate one: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return f.encrypt(raw_key.encode()).decode()


def decrypt_private_key(encrypted: str) -> str:
    """
    Decrypt a stored private key ciphertext.
    Raises RuntimeError on missing key or decryption failure.
    """
    f = _fernet()
    if not f:
        raise RuntimeError("WALLET_ENCRYPTION_KEY not set — cannot decrypt private key")
    try:
        return f.decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise RuntimeError("Private key decryption failed — wrong WALLET_ENCRYPTION_KEY or corrupted data")


def is_encrypted(value: str) -> bool:
    """Heuristic: Fernet tokens start with 'gAAA' (base64url of version byte)."""
    return value.startswith("gAAA") or value.startswith("Z0FBQ")
