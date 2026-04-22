from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import httpx
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Session as DBSession, Wallet
from app.services.polymarket_onboarding_service import (
    CTF,
    PUSD,
    SAFE_MULTISEND,
    USDC,
    ZERO_ADDRESS,
    _encode_multisend,
    _get_safe_nonce,
    _poll_transaction,
    _relayer_post_authed,
    _sign_safe_tx,
)
from app.services.privy_tx_service import PrivyTxError, _privy_rpc

log = logging.getLogger("uvicorn.error")

_ZERO_BYTES32 = b"\x00" * 32
_REDEEM_SELECTOR = keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]


class RedeemService:
    async def redeem_session_winnings(
        self,
        db: AsyncSession,
        session_id: str,
        *,
        trigger: str,
    ) -> dict[str, Any]:
        return;


redeem_service = RedeemService()
