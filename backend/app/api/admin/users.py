"""
admin/users.py — Admin user management.

GET  /admin/users/         — list all users with live balances (from in-memory cache)
PATCH /admin/users/{id}    — update fee_override_bps / display_name
POST /admin/users/{id}/stop-session — soft/hard stop a user's active session
GET  /admin/users/{id}/poly-state   — fetch live Poly open orders + token balances
POST /admin/users/{id}/nuclear-close — cancel orders and/or taker-sell positions on Poly
"""
from __future__ import annotations

import asyncio
import math
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, and_
from typing import Optional, List

from app.core.deps import AdminUser, DbSession
from app.db.models import User, Wallet, Session as DBSession, AuditLog, SessionSnapshot, SessionSlotMarketHistory, SessionSlot
from app.runtime.session_registry import session_registry
from app.runtime.session_manager import session_manager
from app.services.balance_poller import balance_poller
from app.integrations.polymarket.execution_client import ExecutionClient
from app.runtime.market_data_service import market_data_service
from app.runtime.runtime_models import norm
from app.services.nuclear_close_service import run_nuclear_close as _run_nuclear_close
from sqlalchemy import func as _func


async def _user_accurate_stats(db, user_ids: list[str], live_engines: dict) -> dict[str, dict]:
    """
    Compute accurate total_volume + bot_pnl for each user_id from:
    1. max(SessionSnapshot.total_volume) / max(realized_pnl) per stopped session
    2. live engine summary for running sessions
    Returns {user_id: {"volume": float, "pnl": float}}
    """
    if not user_ids:
        return {}

    # All sessions for these users
    sess_q = await db.execute(
        select(DBSession.id, DBSession.user_id, DBSession.status)
        .where(DBSession.user_id.in_(user_ids))
    )
    sessions = sess_q.all()  # (id, user_id, status)
    session_ids = [r.id for r in sessions]
    session_user = {r.id: r.user_id for r in sessions}

    # Best snapshot per session
    snap_q = await db.execute(
        select(
            SessionSnapshot.session_id,
            _func.max(SessionSnapshot.total_volume).label("vol"),
            _func.max(SessionSnapshot.realized_pnl).label("pnl"),
        )
        .where(SessionSnapshot.session_id.in_(session_ids))
        .group_by(SessionSnapshot.session_id)
    )
    snap_by_session = {r.session_id: (float(r.vol or 0), float(r.pnl or 0)) for r in snap_q.all()}

    stats: dict[str, dict] = {uid: {"volume": 0.0, "pnl": 0.0} for uid in user_ids}

    for sess_id, user_id, status in sessions:
        if status in ("running", "starting", "recovering"):
            eng = live_engines.get(sess_id)
            if eng:
                sm = eng.summary
                stats[user_id]["volume"] += sm.total_volume or 0.0
                stats[user_id]["pnl"]    += sm.realized_pnl or 0.0
                continue
        # stopped or no live engine — use snapshot
        vol, pnl = snap_by_session.get(sess_id, (0.0, 0.0))
        stats[user_id]["volume"] += vol
        stats[user_id]["pnl"]    += pnl

    return stats

_LEADERBOARD_URL = "https://data-api.polymarket.com/v1/leaderboard"
_LB_TIMEOUT = 6.0
_LB_TTL = 60.0  # seconds

# cache: user_id -> (fetched_at, {"volume": float, "pnl": float})
_lb_cache: dict[str, tuple[float, dict]] = {}


async def _fetch_leaderboard_one(client, proxy_address: str) -> dict | None:
    """Returns parsed dict on success, None on error/not found."""
    try:
        resp = await asyncio.wait_for(
            client.get(_LEADERBOARD_URL, params={
                "category": "OVERALL", "timePeriod": "ALL",
                "orderBy": "PNL", "limit": "25",
                "user": proxy_address.lower(),
            }),
            timeout=_LB_TIMEOUT,
        )
        if not resp.is_success:
            return None
        data = resp.json()
        entries = data if isinstance(data, list) else data.get("data", [])
        addr_lower = proxy_address.lower()
        for entry in entries:
            ew = (entry.get("proxyWallet") or entry.get("proxyAddress") or entry.get("address") or "").lower()
            if ew == addr_lower:
                vol = float(entry.get("vol") or entry.get("volume") or 0)
                pnl = float(entry.get("pnl") or 0)
                return {"volume": vol, "pnl": pnl}
        return None
    except Exception:
        return None


async def _fetch_leaderboard_batch(proxy_by_user: dict[str, str]) -> dict[str, dict]:
    """Parallel leaderboard fetch for all users. Returns {user_id: {"volume": float, "pnl": float}}.
    Uses 60s TTL cache; on error keeps last known non-zero value."""
    if not proxy_by_user:
        return {}

    import time
    import httpx

    now = time.monotonic()
    user_ids = list(proxy_by_user.keys())

    # Split: stale (need refresh) vs fresh (use cache)
    stale_ids = [uid for uid in user_ids if now - _lb_cache.get(uid, (0.0, {}))[0] >= _LB_TTL]
    fresh_ids  = [uid for uid in user_ids if uid not in stale_ids]

    out = {uid: _lb_cache[uid][1] for uid in fresh_ids}

    if stale_ids:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=4.0, read=8.0, write=2.0, pool=1.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30),
            trust_env=False,
        ) as client:
            results = await asyncio.gather(
                *[_fetch_leaderboard_one(client, proxy_by_user[uid]) for uid in stale_ids],
                return_exceptions=True,
            )
        for uid, r in zip(stale_ids, results):
            prev_val = _lb_cache.get(uid, (0.0, {"volume": 0.0, "pnl": 0.0}))[1]
            if isinstance(r, dict):
                # Keep last non-zero values if new fetch returned zeros
                merged = {
                    "volume": r["volume"] if r["volume"] != 0.0 else prev_val["volume"],
                    "pnl":    r["pnl"]    if r["pnl"]    != 0.0 else prev_val["pnl"],
                }
                _lb_cache[uid] = (now, merged)
                out[uid] = merged
            else:
                # Error: keep previous value, update timestamp only if we had data
                _lb_cache[uid] = (now if prev_val["volume"] == 0.0 else _lb_cache.get(uid, (0.0, prev_val))[0], prev_val)
                out[uid] = prev_val

    return out


router = APIRouter(prefix="/admin/users", tags=["admin:users"])


class PatchUserRequest(BaseModel):
    # fee_override_bps: 0 = builder code OFF (permanent), -1 = clear (builder code ON)
    fee_override_bps: Optional[int] = Field(None, ge=-1, le=0)
    display_name: Optional[str] = None
    # free_fee_days: >0 = builder code OFF for N days, -1 = cancel
    free_fee_days: Optional[int] = Field(None, ge=-1, le=365)


class StopSessionRequest(BaseModel):
    mode: str = "soft"   # "soft" | "hard"


@router.get("/")
async def list_users(
    db: DbSession, admin: AdminUser,
    limit: int = 100, offset: int = 0,
    account_ready: Optional[bool] = None,
    funded: Optional[bool] = None,
):
    # Wait for first cache population if cold (first boot only)
    if not balance_poller._ready.is_set():
        await balance_poller.wait_ready()

    # Single JOIN: users + primary wallets
    rows = await db.execute(
        select(User, Wallet)
        .outerjoin(Wallet, and_(Wallet.user_id == User.id, Wallet.is_primary == True))
        .order_by(User.created_at.desc())
        .limit(limit)
        .offset(offset)
        # No is_active filter — admin sees all users
    )
    pairs = rows.all()   # [(User, Wallet | None), ...]

    user_ids = [u.id for u, _ in pairs]

    # Active sessions in one query
    sessions_q = await db.execute(
        select(DBSession).where(
            DBSession.user_id.in_(user_ids),
            DBSession.status.in_(["pending", "starting", "running", "recovering"]),
        )
    )
    sessions_by_user: dict[str, DBSession] = {}
    for s in sessions_q.scalars().all():
        sessions_by_user.setdefault(s.user_id, s)

    live_engines = {e.session_id: e for e in await session_registry.all()}
    accurate = await _user_accurate_stats(db, user_ids, live_engines)

    # Fetch leaderboard stats for all users with proxy addresses in parallel
    proxy_by_user = {u.id: w.proxy_address for u, w in pairs if w and w.proxy_address}
    leaderboard_by_user = await _fetch_leaderboard_batch(proxy_by_user)

    result = []
    for u, wallet in pairs:
        bal = balance_poller.get(u.id)
        active_session = sessions_by_user.get(u.id)
        session_live = False
        session_status = None
        if active_session:
            session_live = active_session.id in live_engines
            session_status = active_session.status

        positions_value = bal.positions_value  # open positions mark-to-market
        redeem_value    = bal.redeem_value     # redeemable positions value

        us = accurate.get(u.id, {"volume": 0.0, "pnl": 0.0})
        lb = leaderboard_by_user.get(u.id, {"volume": us["volume"], "pnl": us["pnl"]})
        _acct_ready = bool(
            wallet and wallet.onboarding_state == "ready"
            and wallet.proxy_address and wallet.clob_api_key
        )
        _is_funded = bal.trading_balance is not None and bal.trading_balance > 0

        result.append({
            "id":                  u.id,
            "display_name":        u.display_name,
            "is_admin":            u.is_admin,
            "is_active":           u.is_active,
            "fee_override_bps":    u.fee_override_bps,
            "builder_code_enabled": u.fee_override_bps != 0,
            "trading_volume":      round(lb["volume"], 2),
            "bot_pnl":             round(lb["pnl"], 2),
            "wallet_address":      wallet.wallet_address if wallet else None,
            "wallet_id":           wallet.id if wallet else None,
            "proxy_address":       wallet.proxy_address if wallet else None,
            "onboarding_complete": wallet.onboarding_complete if wallet else False,
            "account_ready":       _acct_ready,
            "is_funded":           _is_funded,
            "onboarding_completed_at": wallet.onboarding_completed_at.isoformat() if wallet and wallet.onboarding_completed_at else None,
            "v2_migration_done":   wallet.v2_migration_done if wallet else False,
            "wallet_usdc":         bal.wallet_usdc,
            "wallet_eth":          bal.wallet_eth,
            "wallet_pol":          bal.wallet_pol,
            "trading_balance":     round((bal.trading_balance or 0.0) + positions_value, 2) if bal.trading_balance is not None else None,
            "positions_value":     round(positions_value, 2),
            "redeem_value":        round(redeem_value, 2),
            "active_session_id":   active_session.id if active_session else None,
            "session_status":      session_status,
            "session_live":        session_live,
            "created_at":          u.created_at.isoformat(),
            "free_fee_ends_at":    u.free_fee_ends_at.isoformat() if u.free_fee_ends_at else None,
        })

    if account_ready is not None:
        result = [r for r in result if r["account_ready"] == account_ready]
    if funded is not None:
        result = [r for r in result if r["is_funded"] == funded]

    return result


@router.patch("/{user_id}")
async def patch_user(user_id: str, body: PatchUserRequest, db: DbSession, admin: AdminUser):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    changed = []
    new_builder_code_enabled: Optional[bool] = None
    new_builder_code_expires_at = None

    if body.fee_override_bps is not None:
        if body.fee_override_bps == -1:
            # Clear → builder code ON
            user.fee_override_bps = None
            user.free_fee_ends_at = None
            new_builder_code_enabled = True
            changed.append("builder_code=ON")
        else:
            # 0 → builder code OFF permanent
            user.fee_override_bps = 0
            user.free_fee_ends_at = None
            new_builder_code_enabled = False
            changed.append("builder_code=OFF")

    if body.free_fee_days is not None:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        if body.free_fee_days == -1:
            # Cancel free period → builder code ON
            user.free_fee_ends_at = None
            user.fee_override_bps = None
            new_builder_code_enabled = True
            changed.append("builder_code=ON (free cancelled)")
        else:
            ends_at = _dt.now(_tz.utc) + _td(days=body.free_fee_days)
            user.free_fee_ends_at = ends_at
            user.fee_override_bps = 0
            new_builder_code_enabled = False
            new_builder_code_expires_at = ends_at
            changed.append(f"builder_code=OFF for {body.free_fee_days}d")

    if body.display_name is not None:
        user.display_name = body.display_name
        changed.append(f"display_name={body.display_name!r}")

    if not changed:
        return {"ok": True, "changed": []}

    db.add(AuditLog(
        actor_id=admin["sub"], actor_role="admin",
        action="user.patch", entity_type="user", entity_id=user_id,
        detail=", ".join(changed),
    ))
    await db.commit()

    # Push builder code change to live session engine immediately
    if new_builder_code_enabled is not None:
        s_q = await db.execute(
            select(DBSession).where(
                DBSession.user_id == user_id,
                DBSession.status.in_(["running", "starting", "recovering"]),
            ).limit(1)
        )
        active = s_q.scalars().first()
        if active:
            engine = await session_registry.get(active.id)
            if engine:
                engine._builder_code_enabled = new_builder_code_enabled
                engine._builder_code_expires_at = new_builder_code_expires_at
                try:
                    await engine._client.reinitialize(new_builder_code_enabled)
                except Exception:
                    pass

    return {"ok": True, "changed": changed}


@router.post("/{user_id}/stop-session")
async def stop_user_session(user_id: str, body: StopSessionRequest, db: DbSession, admin: AdminUser):
    s_q = await db.execute(
        select(DBSession).where(
            DBSession.user_id == user_id,
            DBSession.status.in_(["pending", "starting", "running", "recovering"]),
        ).limit(1)
    )
    active = s_q.scalars().first()
    if not active:
        raise HTTPException(404, "No active session for this user")

    actor_id = admin["sub"]
    await session_manager.stop_session(active.id, mode=body.mode, actor_id=actor_id)

    db.add(AuditLog(
        actor_id=actor_id, actor_role="admin",
        action=f"session.{body.mode}_stop", entity_type="session", entity_id=active.id,
        detail=f"admin forced stop for user {user_id}",
    ))
    await db.commit()

    return {"ok": True, "session_id": active.id, "mode": body.mode}


async def _get_token_condition_map(user_id: str, db) -> dict[str, str]:
    """Build {token_id: condition_id} from all SessionSlotMarketHistory for this user."""
    rows = await db.execute(
        select(
            SessionSlotMarketHistory.token_id_yes,
            SessionSlotMarketHistory.token_id_no,
            SessionSlotMarketHistory.condition_id,
        )
        .join(SessionSlot, SessionSlot.id == SessionSlotMarketHistory.session_slot_id)
        .join(DBSession, DBSession.id == SessionSlot.session_id)
        .where(DBSession.user_id == user_id)
        .where(SessionSlotMarketHistory.condition_id.isnot(None))
    )
    result: dict[str, str] = {}
    for row in rows.all():
        cid = row.condition_id
        if row.token_id_yes:
            result[row.token_id_yes] = cid
        if row.token_id_no:
            result[row.token_id_no] = cid
    return result


async def _get_user_client(user_id: str, db) -> ExecutionClient:
    """Build and initialize an ExecutionClient for the user's primary wallet."""
    w_q = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id, Wallet.is_primary == True).limit(1)
    )
    wallet = w_q.scalars().first()
    if not wallet or not wallet.privy_wallet_id:
        raise HTTPException(400, "No primary wallet / privy wallet not linked")
    client = ExecutionClient(
        privy_wallet_id=wallet.privy_wallet_id,
        wallet_address=wallet.wallet_address,
        proxy_address=wallet.proxy_address or "",
    )
    await client.initialize()
    return client


@router.get("/{user_id}/poly-state")
async def get_poly_state(user_id: str, db: DbSession, admin: AdminUser):
    """
    Returns the live Poly state for this user:
    - open_orders: list of {order_id, token_id, side, price, size_remaining}
    - positions: list of {token_id, balance} for any token_id in DB slots + open orders
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    client = await _get_user_client(user_id, db)
    try:
        # Fetch open orders + all positions concurrently
        orders_task    = asyncio.create_task(client.get_open_orders())
        positions_task = asyncio.create_task(client.get_all_positions())
        live_orders, raw_positions = await asyncio.gather(orders_task, positions_task, return_exceptions=False)
        if not isinstance(live_orders, dict):
            live_orders = {}
        if not isinstance(raw_positions, list):
            raw_positions = []

        # token_id → question from positions data-api response
        token_question: dict[str, str] = {
            pos["token_id"]: pos.get("question") or ""
            for pos in raw_positions if pos.get("token_id")
        }

        open_orders_list = []
        for oid, order in live_orders.items():
            orig      = float(order.get("original_size") or 0)
            matched   = float(order.get("size_matched") or 0)
            remaining = max(0.0, orig - matched)
            if remaining < 0.001:
                continue
            tid = order.get("asset_id") or ""
            open_orders_list.append({
                "order_id":       oid,
                "token_id":       tid,
                "question":       token_question.get(tid, ""),
                "side":           (order.get("side") or "").upper(),
                "price":          float(order.get("price") or 0),
                "size_remaining": round(remaining, 4),
            })

        all_tids = list({p["token_id"] for p in raw_positions if p.get("token_id")})

        async def fetch_mid(tid: str):
            try:
                mid = await market_data_service.get_midpoint(tid)
                return tid, mid or 0.0
            except Exception:
                return tid, 0.0

        mid_results = await asyncio.gather(*[fetch_mid(tid) for tid in all_tids])
        mids = dict(mid_results)

        # Build lookup by token_id from raw_positions (data-api already has redeemable + condition_id)
        pos_by_tid = {p["token_id"]: p for p in raw_positions if p.get("token_id")}

        # For resolved positions, check on-chain which contract holds the tokens (NEG_RISK vs CTF)
        # to determine collateral type accurately (pUSD vs USDC.e)
        safe_address = None
        try:
            from app.db.models import Wallet as WalletModel
            wallet_row = await db.execute(
                select(WalletModel).where(WalletModel.user_id == user_id)
            )
            wallet_obj = wallet_row.scalar_one_or_none()
            if wallet_obj:
                safe_address = wallet_obj.proxy_address
        except Exception:
            pass

        resolved_collateral: dict[str, str] = {}
        resolved_tids = [
            p["token_id"] for p in raw_positions
            if p.get("redeemable") and p.get("token_id") and float(p.get("size") or 0) >= 0.01
        ]
        if resolved_tids and safe_address:
            import httpx
            from eth_abi import encode as abi_encode
            from eth_utils import to_checksum_address
            NEG_RISK_ADDR = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
            CTF_ADDR      = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

            async def _erc1155_bal(http: httpx.AsyncClient, contract: str, owner: str, tid: str) -> int:
                try:
                    data = "0x00fdd58e" + abi_encode(
                        ["address", "uint256"],
                        [to_checksum_address(owner), int(tid)],
                    ).hex()
                    r = await http.post(
                        "https://polygon-bor-rpc.publicnode.com",
                        json={"jsonrpc": "2.0", "method": "eth_call",
                              "params": [{"to": contract, "data": data}, "latest"], "id": 1},
                        timeout=5.0,
                    )
                    result = r.json().get("result")
                    return int(result, 16) if result and result not in ("0x", "") else 0
                except Exception:
                    return 0

            async def _check_collateral(tid: str) -> tuple[str, str]:
                async with httpx.AsyncClient(trust_env=False) as http:
                    neg_bal = await _erc1155_bal(http, NEG_RISK_ADDR, safe_address, tid)
                    if neg_bal > 0:
                        return tid, "pUSD"
                    ctf_bal = await _erc1155_bal(http, CTF_ADDR, safe_address, tid)
                    if ctf_bal > 0:
                        return tid, "USDC.e"
                    return tid, "pUSD"  # default: newer markets use pUSD

            collateral_results = await asyncio.gather(*[_check_collateral(tid) for tid in resolved_tids])
            resolved_collateral = dict(collateral_results)

        positions_list = []
        for pos in raw_positions:
            tid = pos.get("token_id") or ""
            bal = float(pos.get("size") or 0)
            if bal < 0.01 or not tid:
                continue
            cur = float(pos.get("cur_price") or 0)
            mid = mids.get(tid, cur)
            price = float(mid) if float(mid) > 0 else cur
            is_resolved = bool(pos.get("redeemable"))
            positions_list.append({
                "token_id":       tid,
                "question":       (pos.get("question") or "").strip(),
                "outcome":        pos.get("outcome") or "",
                "balance":        round(bal, 4),
                "mid":            round(price, 4),
                "value_usd":      round(bal * price, 2),
                "resolved":       is_resolved,
                "condition_id":   pos.get("condition_id") or "",
                "icon":           pos.get("icon") or "",
                "collateral_type": resolved_collateral.get(tid, "") if is_resolved else "",
            })

        total_value_usd = round(sum(p["value_usd"] for p in positions_list), 2)

        return {
            "open_orders":     open_orders_list,
            "positions":       positions_list,
            "total_value_usd": total_value_usd,
        }
    finally:
        await client.close()


class NuclearCloseRequest(BaseModel):
    cancel_orders: bool = True
    cancel_order_ids: List[str] = Field(default_factory=list)  # specific order IDs; if set, only these are cancelled
    sell_token_ids: List[str] = Field(default_factory=list)


@router.post("/{user_id}/nuclear-close")
async def nuclear_close(user_id: str, body: NuclearCloseRequest, db: DbSession, admin: AdminUser):
    """
    Cancel all open orders (if cancel_orders=True) and/or taker-sell
    the specified token positions at current best bid on Poly.
    Works regardless of session state — operates directly on Poly.
    """
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")

    if not body.cancel_orders and not body.cancel_order_ids and not body.sell_token_ids:
        raise HTTPException(400, "Nothing to do")

    client = await _get_user_client(user_id, db)
    client._user_id = user_id
    try:
        results = await _run_nuclear_close(
            client,
            cancel_orders=body.cancel_orders,
            cancel_order_ids=body.cancel_order_ids or None,
            sell_token_ids=body.sell_token_ids or None,
            sell_all_positions=not body.sell_token_ids,
        )

        db.add(AuditLog(
            actor_id=admin["sub"], actor_role="admin",
            action="user.nuclear_close", entity_type="user", entity_id=user_id,
            detail=(
                f"cancelled={len(results['cancelled_orders'])} "
                f"sold={len(results['sold_positions'])} "
                f"redeemed={len(results.get('redeemed_positions', []))} "
                f"errors={len(results['errors'])}"
            ),
        ))
        await db.commit()
    finally:
        await client.close()

    return {"ok": True, **results}


@router.post("/{user_id}/bridge-pol")
async def bridge_pol(user_id: str, db: DbSession, admin: AdminUser):
    """Bridge $1 USDC (Arbitrum) → POL (Polygon) for the user's EOA wallet."""
    from app.services.relay_bridge_service import bridge_pol_for_gas

    w_q = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id, Wallet.is_primary == True).limit(1)
    )
    wallet = w_q.scalars().first()
    if not wallet or not wallet.privy_wallet_id or not wallet.wallet_address:
        raise HTTPException(400, "No primary wallet")

    result = await bridge_pol_for_gas(
        privy_wallet_id=wallet.privy_wallet_id,
        eoa_address=wallet.wallet_address,
    )

    db.add(AuditLog(
        actor_id=admin["sub"], actor_role="admin",
        action="user.bridge_pol", entity_type="user", entity_id=user_id,
        detail=f"ok={result['ok']} tx={result.get('tx_hash') or result.get('error')}",
    ))
    await db.commit()

    return result


@router.post("/{user_id}/fix-clob2")
async def fix_clob2(user_id: str, db: DbSession, admin: AdminUser):
    """
    Fix 'maker address not allowed' on CLOB V2:
    1. Recreate CLOB API key (delete + create)
    2. Initialize Rust client and call update_collateral_allowance to index Safe with CLOB V2
    """
    from app.services.polymarket_onboarding_service import recreate_clob_creds

    w_q = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id, Wallet.is_primary == True).limit(1)
    )
    wallet = w_q.scalars().first()
    if not wallet or not wallet.privy_wallet_id or not wallet.wallet_address or not wallet.proxy_address:
        raise HTTPException(400, "No primary wallet")

    steps = []
    try:
        # Step 1: recreate CLOB API key
        creds = await recreate_clob_creds(
            privy_wallet_id=wallet.privy_wallet_id,
            eoa_address=wallet.wallet_address,
            safe_address=wallet.proxy_address,
        )
        wallet.clob_api_key = creds["key"]
        wallet.clob_api_secret = creds["secret"]
        wallet.clob_api_passphrase = creds["passphrase"]
        steps.append("clob_key_recreated")
        await db.flush()
    except Exception as e:
        raise HTTPException(500, f"CLOB key recreate failed: {e}")

    try:
        # Step 2: update_collateral_allowance via Rust client (indexes Safe pUSD with CLOB V2)
        client = ExecutionClient(
            privy_wallet_id=wallet.privy_wallet_id,
            wallet_address=wallet.wallet_address,
            proxy_address=wallet.proxy_address,
        )
        await client.initialize()
        await client.update_collateral_allowance()
        await client.close()
        steps.append("collateral_indexed")
    except Exception as e:
        # Non-fatal — key was recreated, just log
        steps.append(f"collateral_index_skipped:{str(e)[:60]}")

    db.add(AuditLog(
        actor_id=admin["sub"], actor_role="admin",
        action="user.fix_clob2", entity_type="user", entity_id=user_id,
        detail=f"safe={wallet.proxy_address[:12]} steps={','.join(steps)}",
    ))
    await db.commit()
    return {"ok": True, "steps": steps, "clob_api_key": creds["key"]}


@router.post("/{user_id}/migrate-v2")
async def migrate_v2(user_id: str, db: DbSession, admin: AdminUser):
    """Re-run V2 token approvals (pUSD + CTF × 3 spenders) for the user's Safe."""
    import httpx as _httpx
    from app.services.polymarket_onboarding_service import (
        _build_approval_txns, _encode_multisend, _get_safe_nonce,
        _sign_safe_tx, _relayer_post_authed, _poll_transaction,
        SAFE_MULTISEND, ZERO_ADDRESS,
    )

    w_q = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id, Wallet.is_primary == True).limit(1)
    )
    wallet = w_q.scalars().first()
    if not wallet or not wallet.privy_wallet_id or not wallet.wallet_address or not wallet.proxy_address:
        raise HTTPException(400, "No primary wallet")

    privy_wallet_id = wallet.privy_wallet_id
    eoa_address     = wallet.wallet_address
    safe_address    = wallet.proxy_address

    try:
        async with _httpx.AsyncClient(timeout=60.0, trust_env=False) as http:
            txns = _build_approval_txns()
            multisend_data = _encode_multisend(txns)
            agg = {"to": SAFE_MULTISEND, "data": multisend_data, "value": "0", "operation": 1}

            nonce = await _get_safe_nonce(http, safe_address)
            sig   = await _sign_safe_tx(privy_wallet_id, safe_address,
                                        agg["to"], agg["data"], agg["value"], 1, nonce)
            body = {
                "from": eoa_address, "to": agg["to"], "proxyWallet": safe_address,
                "data": agg["data"], "nonce": nonce, "signature": sig,
                "signatureParams": {"gasPrice": "0", "operation": "1",
                                    "safeTxnGas": "0", "baseGas": "0",
                                    "gasToken": ZERO_ADDRESS, "refundReceiver": ZERO_ADDRESS},
                "type": "SAFE", "metadata": "v2 migration approvals",
            }
            resp = await _relayer_post_authed(http, "/submit", body)
            tx_id = resp.get("transactionID")
            if not tx_id:
                raise HTTPException(500, f"Relayer no tx_id: {resp}")
            result = await _poll_transaction(http, tx_id, max_polls=30)
            if not result:
                raise HTTPException(500, "Approvals tx timeout")

        wallet.v2_migration_done = True
        db.add(AuditLog(
            actor_id=admin["sub"], actor_role="admin",
            action="user.migrate_v2", entity_type="user", entity_id=user_id,
            detail=f"safe={safe_address[:12]} tx={result.get('transactionHash', '')[:16]}",
        ))
        await db.commit()
        return {"ok": True, "tx_hash": result.get("transactionHash")}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
