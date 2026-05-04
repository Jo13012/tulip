"""
nuclear_close_service.py - Shared Polymarket nuclear close logic.
"""
from __future__ import annotations

import asyncio
import math
import logging

import httpx

from app.core.config import settings
from app.runtime.market_data_service import market_data_service
from app.runtime.runtime_models import norm

log = logging.getLogger("nuclear_close")

# Positions with cur_price >= this are considered resolved winners → redeem, not sell
RESOLVED_PRICE_THRESHOLD = 0.95


async def _fetch_condition_id(token_id: str) -> str | None:
    """Fetch condition_id for a token from Gamma API. Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as http:
            resp = await http.get(
                f"{settings.GAMMA_HOST}/markets",
                params={"clobTokenIds": token_id},
            )
            if not resp.is_success:
                return None
            data = resp.json()
            markets = data if isinstance(data, list) else data.get("markets", [])
            for m in markets:
                cid = m.get("conditionId") or m.get("condition_id")
                if cid:
                    return cid
    except Exception:
        pass
    return None


async def run_nuclear_close(
    client,
    *,
    cancel_orders: bool = True,
    cancel_order_ids: list[str] | None = None,
    sell_token_ids: list[str] | None = None,
    sell_all_positions: bool = False,
    # token_condition_map: explicit override {token_id: condition_id} for redeem
    token_condition_map: dict[str, str] | None = None,
) -> dict:
    """
    Cancel open orders and taker-sell selected/all positions directly on Polymarket.
    For resolved winning positions (no bids or cur_price >= 0.95), attempt on-chain redeem.
    """
    specific_oid_set = set(cancel_order_ids or [])
    sell_token_set = set(sell_token_ids or [])
    cond_map: dict[str, str] = dict(token_condition_map or {})

    results = {
        "cancelled_orders": [],
        "sold_positions": [],
        "redeemed_positions": [],
        "errors": [],
    }

    # ── Gather all positions if needed ───────────────────────────────────────────
    all_positions: list[dict] = []
    if sell_all_positions:
        try:
            all_positions = await client.get_all_positions()
        except Exception as e:
            results["errors"].append(f"get_all_positions failed: {e}")

        for pos in all_positions:
            token_id = pos.get("token_id") or ""
            size = float(pos.get("size") or 0.0)
            cur_price = float(pos.get("cur_price") or 0.0)
            if not token_id or size < 0.01:
                continue
            if cur_price >= RESOLVED_PRICE_THRESHOLD:
                # Looks like a resolved winner — redeem instead of sell
                pass
            elif size >= 0.5:
                sell_token_set.add(token_id)

    if not cancel_orders and not specific_oid_set and not sell_token_set:
        # Still may want to redeem — check below
        pass

    # ── Cancel orders ─────────────────────────────────────────────────────────────
    try:
        live_orders = await client.get_open_orders()
    except Exception as e:
        results["errors"].append(f"get_open_orders failed: {e}")
        live_orders = {}

    for oid, order in live_orders.items():
        token_id = order.get("asset_id") or ""
        should_cancel = (
            cancel_orders
            or (token_id in sell_token_set)
            or (specific_oid_set and oid in specific_oid_set)
        )
        if not should_cancel:
            continue
        try:
            await client.cancel_order(oid)
            results["cancelled_orders"].append(oid)
        except Exception as e:
            results["errors"].append(f"cancel {oid[:14]}: {e}")

    if sell_token_set:
        await asyncio.sleep(2.0)

    # ── Taker-sell positions ──────────────────────────────────────────────────────
    no_bid_token_ids: list[str] = []

    for token_id in sell_token_set:
        try:
            balance = await client.get_token_balance(token_id)
            if not balance or balance < 0.5:
                # Small balance — might still be a resolved winner worth redeeming
                no_bid_token_ids.append(token_id)
                continue

            sell_tokens = float(math.floor(balance))
            if sell_tokens < 1.0:
                no_bid_token_ids.append(token_id)
                continue

            book = await market_data_service.get_order_book(token_id)
            bids = book.get("bids") or []
            if not bids:
                # No bids = market likely resolved → try redeem
                no_bid_token_ids.append(token_id)
                continue

            bid_price = norm(float(bids[0]["price"]))
            await client.ensure_sell_allowance(token_id)
            resp = await client.place_limit_order(
                token_id=token_id,
                price=bid_price,
                size=sell_tokens,
                side="SELL",
                tick_size="0.001",
                neg_risk=False,
            )
            results["sold_positions"].append({
                "token_id": token_id,
                "tokens": sell_tokens,
                "price": bid_price,
                "estimated_usdc": round(sell_tokens * bid_price, 2),
                "order_id": resp.get("orderID"),
                "success": resp.get("success", False),
            })
        except Exception as e:
            results["errors"].append(f"sell {token_id[:16]}: {e}")

    # Also queue any resolved winners from all_positions (cur_price >= threshold)
    if sell_all_positions:
        for pos in all_positions:
            token_id = pos.get("token_id") or ""
            cur_price = float(pos.get("cur_price") or 0.0)
            size = float(pos.get("size") or 0.0)
            if token_id and size >= 0.01 and cur_price >= RESOLVED_PRICE_THRESHOLD:
                if token_id not in no_bid_token_ids:
                    no_bid_token_ids.append(token_id)

    # ── Redeem resolved positions ─────────────────────────────────────────────────
    redeem_candidates = [t for t in no_bid_token_ids if t]
    has_redeem_creds = bool(
        getattr(client, "privy_wallet_id", None)
        and getattr(client, "wallet_address", None)
        and getattr(client, "proxy_address", None)
    )

    if redeem_candidates and has_redeem_creds:
        from app.services.redeem_service import redeem_positions

        privy_wallet_id = client.privy_wallet_id
        eoa_address = client.wallet_address
        safe_address = client.proxy_address

        # Fetch condition_ids concurrently for all candidates
        for token_id in redeem_candidates:
            if token_id not in cond_map:
                condition_id = await _fetch_condition_id(token_id)
                if condition_id:
                    cond_map[token_id] = condition_id

        # Deduplicate by condition_id
        seen_conditions: set[str] = set()
        for token_id in redeem_candidates:
            condition_id = cond_map.get(token_id)
            if not condition_id or condition_id in seen_conditions:
                if not condition_id:
                    results["errors"].append(f"redeem {token_id[:16]}: no condition_id found")
                continue
            seen_conditions.add(condition_id)

            # Estimate tokens from balance
            try:
                balance = await client.get_token_balance(token_id)
                estimated_tokens = float(balance or 0.0)
            except Exception:
                estimated_tokens = 0.0

            redeem_result = await redeem_positions(
                privy_wallet_id=privy_wallet_id,
                eoa_address=eoa_address,
                safe_address=safe_address,
                condition_id=condition_id,
                estimated_tokens=estimated_tokens,
            )
            results["redeemed_positions"].append({
                "token_id": token_id,
                "condition_id": condition_id,
                "estimated_usdc": round(estimated_tokens, 2),
                "ok": redeem_result["ok"],
                "tx_hash": redeem_result.get("tx_hash"),
                "error": redeem_result.get("error"),
            })
    elif redeem_candidates and not has_redeem_creds:
        for token_id in redeem_candidates:
            results["errors"].append(f"token {token_id[:16]}: no bids in book (market resolved, redeem not configured)")

    return results
