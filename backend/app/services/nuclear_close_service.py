"""
nuclear_close_service.py - Shared Polymarket nuclear close logic.
"""
from __future__ import annotations

import asyncio
import math
import logging

from app.runtime.market_data_service import market_data_service
from app.runtime.runtime_models import norm

log = logging.getLogger("nuclear_close")


async def run_nuclear_close(
    client,
    *,
    cancel_orders: bool = True,
    cancel_order_ids: list[str] | None = None,
    sell_token_ids: list[str] | None = None,
    sell_all_positions: bool = False,
) -> dict:
    """
    Cancel open orders and taker-sell positions.
    Positions flagged redeemable=True by data-api are redeemed on-chain instead of sold.
    """
    specific_oid_set = set(cancel_order_ids or [])
    sell_token_set = set(sell_token_ids or [])

    results = {
        "cancelled_orders": [],
        "sold_positions": [],
        "redeemed_positions": [],
        "errors": [],
    }

    # ── Gather all positions ──────────────────────────────────────────────────────
    # Always fetch — needed for condition_id map + redeemable flags
    all_positions: list[dict] = []
    try:
        all_positions = await client.get_all_positions()
    except Exception as e:
        results["errors"].append(f"get_all_positions failed: {e}")

    if sell_all_positions:
        for pos in all_positions:
            token_id = pos.get("token_id") or ""
            size = float(pos.get("size") or 0.0)
            if token_id and size >= 0.01:
                sell_token_set.add(token_id)

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

    # Build maps from positions: token_id → condition_id
    # redeemable_map: only data-api confirmed redeemable
    # condition_map: all positions that have a condition_id (for fallback)
    redeemable_map: dict[str, str] = {
        pos["token_id"]: pos["condition_id"]
        for pos in all_positions
        if pos.get("redeemable") and pos.get("condition_id") and pos.get("token_id")
    }
    condition_map: dict[str, str] = {
        pos["token_id"]: pos["condition_id"]
        for pos in all_positions
        if pos.get("condition_id") and pos.get("token_id")
    }
    log.info(
        "nuclear_close: proxy=%s eoa=%s sell_tokens=%d redeemable=%d condition_ids=%d all_positions=%d",
        getattr(client, "proxy_address", "?")[:12],
        getattr(client, "wallet_address", "?")[:12],
        len(sell_token_set), len(redeemable_map), len(condition_map), len(all_positions),
    )

    # ── Taker-sell or redeem each position ────────────────────────────────────────
    # tuple: (token_id, condition_id, size, outcome)  — outcome used to pick indexSet
    redeemable_to_process: list[tuple[str, str, float, str]] = []

    # Build outcome map: token_id → outcome string (YES/No/etc)
    outcome_map: dict[str, str] = {
        pos["token_id"]: (pos.get("outcome") or "").upper()
        for pos in all_positions if pos.get("token_id")
    }

    for token_id in sell_token_set:
        # If data-api says redeemable, queue for on-chain redeem
        if token_id in redeemable_map:
            pos = next((p for p in all_positions if p.get("token_id") == token_id), {})
            size = float(pos.get("size") or 0.0)
            outcome = (pos.get("outcome") or "").upper()
            redeemable_to_process.append((token_id, redeemable_map[token_id], size, outcome))
            continue

        try:
            balance = await client.get_token_balance(token_id)
            bal = float(balance or 0.0)

            book = await market_data_service.get_order_book(token_id)
            bids = book.get("bids") or []

            if not bids:
                # No bids = market closed. Try redeem even if data-api hasn't flagged it yet.
                cid = condition_map.get(token_id)
                if cid:
                    outcome = outcome_map.get(token_id, "")
                    log.info("nuclear_close: no bids fallback redeem token=%s cid=%s outcome=%s bal=%.4f",
                             token_id[:16], cid[:20], outcome, bal)
                    redeemable_to_process.append((token_id, cid, bal, outcome))
                else:
                    results["errors"].append(
                        f"token {token_id[:16]}: no bids, no condition_id (cannot redeem)"
                    )
                continue

            if bal < 0.5:
                results["errors"].append(f"token {token_id[:16]}: balance {bal:.2f} too low to sell")
                continue

            sell_tokens = float(math.floor(bal))
            if sell_tokens < 1.0:
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

    # ── Claim ALL redeemable positions (from data-api, not just sell_token_set) ──
    # Mirrors claimAllRedeemablePositions.ts: fetch all redeemable, group by condition_id
    all_redeemable = [
        pos for pos in all_positions
        if pos.get("redeemable") and pos.get("condition_id") and pos.get("token_id")
    ]
    # Merge with redeemable_to_process (fallback no-bids positions)
    extra_cids = {cid for _, cid, _, _ in redeemable_to_process}
    for pos in all_redeemable:
        tid = pos["token_id"]
        cid = pos["condition_id"]
        if cid not in extra_cids and tid not in {t for t, _, _, _ in redeemable_to_process}:
            size = float(pos.get("size") or 0.0)
            outcome = (pos.get("outcome") or "").upper()
            redeemable_to_process.append((tid, cid, size, outcome))
            extra_cids.add(cid)

    # Deduplicate by condition_id (redeem once per condition, not per token)
    seen_conditions: set[str] = set()
    deduped: list[tuple[str, str, float, str]] = []
    for token_id, condition_id, size, outcome in redeemable_to_process:
        if condition_id not in seen_conditions:
            seen_conditions.add(condition_id)
            deduped.append((token_id, condition_id, size, outcome))

    if deduped:
        from app.services.redeem_service import redeem_positions
        privy_wallet_id = getattr(client, "privy_wallet_id", None)
        eoa_address = getattr(client, "wallet_address", None)
        safe_address = getattr(client, "proxy_address", None)

        if not (privy_wallet_id and eoa_address and safe_address):
            log.error("nuclear_close: cannot redeem — missing wallet credentials")
        else:
            for token_id, condition_id, size, outcome in deduped:
                log.info("nuclear_close: redeeming condition=%s size=%.2f outcome=%s",
                         condition_id[:20], size, outcome)
                try:
                    res = await redeem_positions(
                        privy_wallet_id=privy_wallet_id,
                        eoa_address=eoa_address,
                        safe_address=safe_address,
                        condition_id=condition_id,
                        estimated_tokens=size,
                        known_token_id=token_id,
                        outcome=outcome,
                    )
                    results["redeemed_positions"].append({
                        "token_id": token_id,
                        "condition_id": condition_id,
                        "size": size,
                        "ok": res.get("ok"),
                        "tx_hash": res.get("tx_hash"),
                        "error": res.get("error"),
                    })
                except Exception as e:
                    results["errors"].append(f"redeem {condition_id[:20]}: {e}")

    return results
