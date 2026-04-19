"""
deps.py — FastAPI dependency injectors.

Auth model:
  - Every authenticated request carries the Privy access token (or dev token).
  - verify_privy_token() validates it.
  - We resolve the DB user from the verified privy_user_id claim.
  - is_admin comes from DB, not from the token.
"""
from __future__ import annotations
from typing import Annotated, AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_privy_token
from app.db.models import User
from app.db.session import async_session_factory

bearer_scheme = HTTPBearer(auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict:
    """
    Validate the Bearer token and return a dict with:
      sub        — DB user id (UUID string)
      privy_id   — Privy DID / user id
      is_admin   — from DB users.is_admin
      email      — from DB
    """
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    claims = await verify_privy_token(credentials.credentials)
    if not claims:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    privy_user_id = claims.get("sub") or claims.get("userId", "")
    if not privy_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject")

    try:
        async with async_session_factory() as db:
            q = await db.execute(select(User).where(User.privy_user_id == privy_user_id))
            user = q.scalars().first()
    except Exception:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found — call /api/v1/auth/sync first")

    return {
        "sub": user.id,
        "privy_id": privy_user_id,
        "is_admin": user.is_admin,
    }


async def require_admin(
    user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


CurrentUser = Annotated[dict, Depends(get_current_user)]
AdminUser   = Annotated[dict, Depends(require_admin)]
DbSession   = Annotated[AsyncSession, Depends(get_db)]
