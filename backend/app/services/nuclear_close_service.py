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


async def _fetch_market_info(token_id: str) -> tuple[str | None, bool]:
    """
    Fetch (condition_id, closed) for a token from CLOB API.
    closed=True means the market's order book is closed = resolution happened.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as http:
            resp = await http.get(
                "https://clob.polymarket.com/markets",
                params={"token_id": token_id},
            )
            if not resp.is_success:
                return None, False
            data = resp.json()
            markets = data.get("data") or []
            for m in markets:
                cid = m.get("condition_id")
                closed = bool(m.get("closed"))
                if cid:
                    return cid, closed
    except Exception:
        pass
    return None, False


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
    For positions with no bids in book (market resolved/closed), attempt on-chain redeem.
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
            if token_id and size >= 0.5:
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

    # ── Redeem resolved positions ─────────────────────────────────────────────────
    redeem_candidates = [t for t in no_bid_token_ids if t]
    has_redeem_creds = bool(
        getattr(client, "privy_wallet_id", None)
        and getattr(client, "wallet_address", None)
        and getattr(client, "proxy_address", None)
    )

    log.info("nuclear_close: no_bid_candidates=%d has_creds=%s", len(redeem_candidates), has_redeem_creds)

    if redeem_candidates:
        from app.services.redeem_service import redeem_positions

        privy_wallet_id = client.privy_wallet_id if has_redeem_creds else None
        eoa_address = client.wallet_address if has_redeem_creds else None
        safe_address = client.proxy_address if has_redeem_creds else None

        # Fetch condition_id + closed status from Gamma for each candidate
        closed_map: dict[str, bool] = {}
        for token_id in redeem_candidates:
            if token_id in cond_map:
                # condition_id already known — still need closed status
                _, closed = await _fetch_market_info(token_id)
                closed_map[token_id] = closed
            else:
                condition_id, closed = await _fetch_market_info(token_id)
                closed_map[token_id] = closed
                log.info("nuclear_close gamma: token=%s condition=%s closed=%s",
                         token_id[:16], condition_id and condition_id[:20], closed)
                if condition_id:
                    cond_map[token_id] = condition_id

        # Deduplicate by condition_id, only redeem truly closed markets
        seen_conditions: set[str] = set()
        for token_id in redeem_candidates:
            is_closed = closed_map.get(token_id, False)
            condition_id = cond_map.get(token_id)

            if not is_closed:
                results["errors"].append(f"token {token_id[:16]}: no bids in book (market active, not resolved)")
                log.info("nuclear_close: token=%s no bids but market still active", token_id[:16])
                continue

            if not condition_id:
                results["errors"].append(f"token {token_id[:16]}: market closed but no condition_id found")
                continue

            if condition_id in seen_conditions:
                continue
            seen_conditions.add(condition_id)

            if not has_redeem_creds:
                results["errors"].append(f"token {token_id[:16]}: market resolved but no wallet creds for redeem")
                continue

            try:
                balance = await client.get_token_balance(token_id)
                estimated_tokens = float(balance or 0.0)
            except Exception:
                estimated_tokens = 0.0

            log.info("nuclear_close: redeeming condition=%s tokens=%.4f", condition_id[:20], estimated_tokens)
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

    return results
