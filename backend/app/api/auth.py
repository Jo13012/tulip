"""
auth.py — Auth bootstrap endpoints.

No custom JWT issuance. Frontend sends its Privy access token on every request.
Backend verifies the Privy token directly on every authenticated route.

Endpoints:
  POST /api/v1/auth/sync   — upsert user + wallet from Privy token → return app profile
  GET  /api/v1/auth/me     — return current user profile (requires valid token)

Dev mode only:
  POST /api/v1/auth/dev-token  — mint a local HS256 dev token (DEV_AUTH_ENABLED=true)
"""
from __future__ import annotations
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from jose import jwt
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.security import verify_privy_token
from app.db.models import User, Wallet
from app.db.session import async_session_factory
from app.services.privy_wallet_service import privy_wallet_service, PrivyWalletError
from app.services.polymarket_onboarding_service import onboard_wallet

router = APIRouter(prefix="/auth", tags=["auth"])


def _clean_onboarding_error(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    return message[:500]


# ── Shared response model ─────────────────────────────────────────────────────

class UserProfile(BaseModel):
    user_id: str
    privy_user_id: str
    display_name: str = ""
    is_admin: bool
    wallet_id: str = ""
    wallet_address: str = ""
    onboarding_complete: bool = False


class UpdateDisplayNameRequest(BaseModel):
    display_name: str = Field(min_length=3, max_length=15, pattern=r"^[A-Za-z0-9]+$")


# ── Dev token (local dev only) ────────────────────────────────────────────────

class DevTokenRequest(BaseModel):
    role: str = "user"   # "user" | "admin"


class DevTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    privy_user_id: str
    is_admin: bool


@router.post("/dev-token", response_model=DevTokenResponse)
async def dev_token(body: DevTokenRequest):
    """
    Dev-only endpoint. Creates/reuses a local dev user and returns a signed HS256 token.
    The token is used as the Bearer token on all subsequent requests.
    Disabled in production (DEV_AUTH_ENABLED=false).
    """
    if not settings.DEV_AUTH_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    is_admin = body.role == "admin"
    privy_id = f"dev_{body.role}"

    async with async_session_factory() as db:
        q = await db.execute(select(User).where(User.privy_user_id == privy_id))
        user = q.scalars().first()
        if not user:
            user = User(
                privy_user_id=privy_id,
                display_name="Dev Admin" if is_admin else "Dev User",
                is_admin=is_admin,
            )
            db.add(user)
            await db.flush()

            wallet = Wallet(
                user_id=user.id,
                wallet_address="0x" + "0" * 40,
                proxy_address="",
                is_primary=True,
            )
            db.add(wallet)
            await db.commit()
            await db.refresh(user)

    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    token = jwt.encode(
        {
            "sub": privy_id,
            "exp": expire,
            "iat": datetime.now(timezone.utc),
            "jti": str(uuid.uuid4()),
        },
        settings.DEV_JWT_SECRET,
        algorithm="HS256",
    )

    return DevTokenResponse(
        access_token=token,
        user_id=user.id,
        privy_user_id=privy_id,
        is_admin=user.is_admin,
    )


# ── Sync ──────────────────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    wallet_address: str = ""
    proxy_address: str = ""


@router.post("/sync", response_model=UserProfile)
async def sync_user(request: Request, body: SyncRequest):
    """
    Called once after Privy login (or dev-token flow).
    Frontend: Authorization: Bearer <privy_access_token or dev_token>
    Backend: verifies token, upserts user + wallet in DB, returns profile.

    This endpoint intentionally does NOT use the standard CurrentUser dep
    because the user may not exist in DB yet (first login).
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = auth_header[len("Bearer "):]
    claims = await verify_privy_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    privy_user_id = claims.get("sub") or claims.get("userId", "")
    if not privy_user_id:
        raise HTTPException(status_code=401, detail="Token missing subject")

    async with async_session_factory() as db:
        q = await db.execute(select(User).where(User.privy_user_id == privy_user_id))
        user = q.scalars().first()

        if not user:
            user = User(
                privy_user_id=privy_user_id,
                is_admin=False,
            )
            db.add(user)
            await db.flush()

        # Look up existing wallet in DB first (avoid creating a new Privy wallet on every login)
        primary_wallet_address = ""
        primary_wallet_id = ""

        wq = await db.execute(
            select(Wallet).where(
                Wallet.user_id == user.id,
                Wallet.is_primary == True,
            )
        )
        existing_wallet = wq.scalars().first()

        if existing_wallet:
            primary_wallet_address = existing_wallet.wallet_address
            primary_wallet_id = existing_wallet.id

            # Retry onboarding if it previously failed
            if existing_wallet.onboarding_state in ("pending", "failed") and existing_wallet.privy_wallet_id:
                import logging, traceback
                log = logging.getLogger("auth")
                log.info("Retrying onboarding for wallet %s (state=%s)", existing_wallet.id, existing_wallet.onboarding_state)
                try:
                    existing_wallet.onboarding_state = "deploying"
                    existing_wallet.onboarding_error_detail = None
                    await db.flush()

                    async def update_retry_stage(stage: str) -> None:
                        existing_wallet.onboarding_state = stage
                        existing_wallet.onboarding_error_detail = None
                        await db.flush()

                    onboarding = await onboard_wallet(
                        existing_wallet.privy_wallet_id,
                        existing_wallet.wallet_address,
                        on_stage=update_retry_stage,
                    )
                    existing_wallet.proxy_address = onboarding["proxy_address"]
                    existing_wallet.clob_api_key = onboarding["clob_api_key"]
                    existing_wallet.clob_api_secret = onboarding["clob_api_secret"]
                    existing_wallet.clob_api_passphrase = onboarding["clob_api_passphrase"]
                    existing_wallet.onboarding_state = "finalizing"
                    existing_wallet.onboarding_error_detail = None
                    await db.flush()
                    existing_wallet.onboarding_state = "ready"
                    log.info("Onboarding retry succeeded for wallet %s", existing_wallet.id)
                except Exception as ob_err:
                    existing_wallet.onboarding_state = "failed"
                    existing_wallet.onboarding_error_detail = _clean_onboarding_error(ob_err)
                    log.error("Onboarding retry failed: %s\n%s", ob_err, traceback.format_exc())

        elif settings.PRIVY_APP_ID and settings.PRIVY_APP_SECRET:
            # First login — reuse existing Privy wallet if present, otherwise create.
            import logging, traceback
            log = logging.getLogger("auth")
            try:
                privy_wallet_data = await privy_wallet_service.get_or_create_wallet(
                    privy_user_id
                )
                wallet_address = privy_wallet_data["wallet_address"]
                privy_wid = privy_wallet_data["privy_wallet_id"]
                existing = await db.execute(
                    select(Wallet).where(
                        Wallet.user_id == user.id,
                        (Wallet.privy_wallet_id == privy_wid) | (Wallet.wallet_address == wallet_address),
                    )
                )
                existing_wallet = existing.scalars().first()

                if existing_wallet:
                    existing_wallet.privy_wallet_id = existing_wallet.privy_wallet_id or privy_wid
                    existing_wallet.wallet_address = existing_wallet.wallet_address or wallet_address
                    existing_wallet.is_primary = True
                    existing_wallet.onboarding_error_detail = None
                    primary_wallet_address = existing_wallet.wallet_address
                    primary_wallet_id = existing_wallet.id
                else:
                    new_wallet = Wallet(
                        user_id=user.id,
                        wallet_address=wallet_address,
                        privy_wallet_id=privy_wid,
                        is_primary=True,
                        onboarding_state="pending",
                        onboarding_error_detail=None,
                    )
                    db.add(new_wallet)
                    await db.flush()
                    primary_wallet_address = wallet_address
                    primary_wallet_id = new_wallet.id

                if not user.display_name:
                    user.display_name = f"{wallet_address[:6]}…{wallet_address[-4:]}"

                # Polymarket onboarding (Safe deploy + approvals + CLOB creds)
                try:
                    target_wallet = existing_wallet if existing_wallet else new_wallet
                    target_wallet.onboarding_state = "deploying"
                    target_wallet.onboarding_error_detail = None
                    await db.flush()
                    log.info("Starting onboarding for wallet %s", wallet_address)

                    async def update_stage(stage: str) -> None:
                        target_wallet.onboarding_state = stage
                        target_wallet.onboarding_error_detail = None
                        await db.flush()

                    onboarding = await onboard_wallet(privy_wid, wallet_address, on_stage=update_stage)
                    target_wallet.onboarding_state = "finalizing"
                    target_wallet.onboarding_error_detail = None
                    await db.flush()
                    target_wallet.proxy_address = onboarding["proxy_address"]
                    target_wallet.clob_api_key = onboarding["clob_api_key"]
                    target_wallet.clob_api_secret = onboarding["clob_api_secret"]
                    target_wallet.clob_api_passphrase = onboarding["clob_api_passphrase"]
                    target_wallet.onboarding_state = "ready"
                    # Cache Polymarket deposit address once for future bridges
                    try:
                        from app.integrations.polymarket.bridge_client import bridge_client
                        seed_address = target_wallet.proxy_address or ""
                        if seed_address and not target_wallet.funding_deposit_address_cached:
                            deposit_addresses = await bridge_client.create_deposit_addresses(seed_address)
                            address_block = deposit_addresses.get("address") or {}
                            deposit_address = address_block.get("evm") or ""
                            if deposit_address:
                                target_wallet.funding_deposit_address_cached = deposit_address
                                target_wallet.funding_deposit_cached_at = datetime.now(timezone.utc)
                    except Exception as cache_err:
                        log.warning("Deposit address pre-cache failed: %s", cache_err)
                    log.info("Onboarding succeeded for wallet %s", wallet_address)
                except Exception as ob_err:
                    log.error("Polymarket onboarding failed: %s\n%s", ob_err, traceback.format_exc())
                    target_wallet = existing_wallet if existing_wallet else new_wallet
                    target_wallet.onboarding_state = "failed"
                    target_wallet.onboarding_error_detail = _clean_onboarding_error(ob_err)

            except PrivyWalletError as e:
                raise HTTPException(status_code=502, detail=f"Privy wallet creation failed: {e}")

        await db.commit()
        await db.refresh(user)

    return UserProfile(
        user_id=user.id,
        privy_user_id=privy_user_id,
        display_name=user.display_name or "",
        is_admin=user.is_admin,
        wallet_id=primary_wallet_id,
        wallet_address=primary_wallet_address,
        onboarding_complete=existing_wallet.onboarding_complete if existing_wallet else False,
    )


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserProfile)
async def get_me(user: CurrentUser):
    """Return current authenticated user profile. Used for app bootstrap after page reload."""
    async with async_session_factory() as db:
        wq = await db.execute(
            select(Wallet).where(
                Wallet.user_id == user["sub"],
                Wallet.is_primary == True,
            )
        )
        wallet = wq.scalars().first()
        uq = await db.execute(select(User).where(User.id == user["sub"]))
        db_user = uq.scalars().first()

    return UserProfile(
        user_id=user["sub"],
        privy_user_id=user["privy_id"],
        display_name=db_user.display_name if db_user else "",
        is_admin=user["is_admin"],
        wallet_id=wallet.id if wallet else "",
        wallet_address=wallet.wallet_address if wallet else "",
        onboarding_complete=wallet.onboarding_complete if wallet else False,
    )


@router.patch("/profile", response_model=UserProfile)
async def update_profile(body: UpdateDisplayNameRequest, user: CurrentUser):
    async with async_session_factory() as db:
        uq = await db.execute(select(User).where(User.id == user["sub"]))
        db_user = uq.scalars().first()
        if not db_user:
            raise HTTPException(status_code=404, detail="User not found")

        now = datetime.now(timezone.utc)
        if db_user.display_name != body.display_name:
            last_update = db_user.display_name_updated_at
            if last_update:
                elapsed = now - last_update
                if elapsed < timedelta(hours=12):
                    remaining = timedelta(hours=12) - elapsed
                    hours = int(remaining.total_seconds() // 3600)
                    minutes = int((remaining.total_seconds() % 3600) // 60)
                    raise HTTPException(
                        status_code=429,
                        detail=f"Username can be changed once every 12 hours. Try again in {hours}h {minutes}m.",
                    )
            db_user.display_name = body.display_name
            db_user.display_name_updated_at = now
        await db.commit()
        await db.refresh(db_user)

        wq = await db.execute(
            select(Wallet).where(
                Wallet.user_id == db_user.id,
                Wallet.is_primary == True,
            )
        )
        wallet = wq.scalars().first()

    return UserProfile(
        user_id=db_user.id,
        privy_user_id=db_user.privy_user_id or "",
        display_name=db_user.display_name or "",
        is_admin=db_user.is_admin,
        wallet_id=wallet.id if wallet else "",
        wallet_address=wallet.wallet_address if wallet else "",
        onboarding_complete=wallet.onboarding_complete if wallet else False,
    )
