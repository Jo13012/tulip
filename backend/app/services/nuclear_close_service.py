"""
nuclear_close_service.py - Shared Polymarket nuclear close logic.
"""
from __future__ import annotations

import asyncio
import math

from app.runtime.market_data_service import market_data_service
from app.runtime.runtime_models import norm


async def run_nuclear_close(
    client,
    *,
    cancel_orders: bool = True,
    cancel_order_ids: list[str] | None = None,
    sell_token_ids: list[str] | None = None,
    sell_all_positions: bool = False,
) -> dict:
    """
    Cancel open orders and taker-sell selected/all positions directly on Polymarket.
    """
    specific_oid_set = set(cancel_order_ids or [])
    sell_token_set = set(sell_token_ids or [])

    results = {"cancelled_orders": [], "sold_positions": [], "errors": []}

    if sell_all_positions:
        try:
            positions = await client.get_all_positions()
            for pos in positions:
                token_id = pos.get("token_id") or ""
                size = float(pos.get("size") or 0.0)
                if token_id and size >= 0.5:
                    sell_token_set.add(token_id)
        except Exception as e:
            results["errors"].append(f"get_all_positions failed: {e}")

    if not cancel_orders and not specific_oid_set and not sell_token_set:
        return results

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

    for token_id in sell_token_set:
        try:
            balance = await client.get_token_balance(token_id)
            if not balance or balance < 0.5:
                results["errors"].append(f"token {token_id[:16]}: balance {float(balance or 0.0):.2f} too low")
                continue

            sell_tokens = float(math.floor(balance))
            if sell_tokens < 1.0:
                continue

            book = await market_data_service.get_order_book(token_id)
            bids = book.get("bids") or []
            if not bids:
                results["errors"].append(f"token {token_id[:16]}: no bids in book")
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
                "order_id": resp.get("orderID"),
                "success": resp.get("success", False),
            })
        except Exception as e:
            results["errors"].append(f"sell {token_id[:16]}: {e}")

    return results
