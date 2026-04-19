"""
security.py — Privy access token verification (ES256).
"""
from __future__ import annotations
from typing import Optional

from jose import JWTError, jwt
from app.core.config import settings


async def verify_privy_token(token: str) -> Optional[dict]:
    if not settings.PRIVY_VERIFICATION_KEY:
        return None

    key = settings.PRIVY_VERIFICATION_KEY.strip()
    if not key.startswith("-----"):
        key = f"-----BEGIN PUBLIC KEY-----\n{key}\n-----END PUBLIC KEY-----"

    try:
        return jwt.decode(
            token,
            key,
            algorithms=["ES256"],
            audience=settings.PRIVY_APP_ID,
        )
    except JWTError:
        return None
