"""
session_slot_runtime.py — Per-slot trading runtime.

This is the adapted core from mono_user_bot/backend/engine/trading_engine.py.
It is NOT a global singleton. One instance per active session slot.

All engine behaviors preserved exactly:
  - real_tick(), norm(), grid_levels()
  - maker buy grid execution
  - TP maker sell flow
  - taker fallback
  - stop-loss
  - rebalance
  - partial BUY fill handling via matched delta
  - partial TP SELL fill handling via matched delta
  - _pending_buy_capital
  - POST_CANCEL_SETTLE, BUY_FAIL_COOLDOWN, TP_REPOST_INTERVAL
  - ensure_sell_allowance()
  - USDC cache TTL logic
  - dust lot / phantom close
  - spread_from_ask
  - bulk get_open_orders() anti-spam
  - selective get_order_status() only when needed

Key difference vs mono_user_bot:
  - No global registry, no global poly_client, no global log_store
  - Uses per-session ExecutionClient
  - Uses shared MarketDataService for read-only data
  - Reports state changes via callback
  - SlotState transitions for slot replacement lifecycle
"""
from __future__ import annotations
import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Callable, Awaitable, Optional, Set

from app.runtime.runtime_models import (
    RuntimeLot, LotStatus, SlotMarketConfig, SlotState,
    real_tick, norm, h, grid_levels,
)
from app.integrations.polymarket.execution_client import ExecutionClient
from app.runtime.market_data_service import market_data_service, MarketClosedError

# ── Constants (identical to mono_user_bot) ────────────────────────────────────

POLL_S            = 0.8
BUY_FAIL_COOLDOWN = 45.0
POST_CANCEL_SETTLE = 5.0
TP_REPOST_INTERVAL = 30.0
POLY_MIN_ORDER_USDC = 5.0  # Polymarket minimum order size

# Type aliases
OnLotOpenedFn  = Callable[[RuntimeLot], Awaitable[None]]
OnLotClosedFn  = Callable[[RuntimeLot, float], Awaitable[None]]  # lot, pnl
OnStateFn      = Callable[[SlotState, str], Awaitable[None]]     # state, reason
OnLogFn        = Callable[[str, str], Awaitable[None]]           # level, message


class SessionSlotRuntime:
    """
    Per-slot trading runtime. Manages the full grid MM lifecycle for one slot.

    Lifecycle:
      start() → runs _market_loop() → stop() / replace_market()
    """

    def __init__(
        self,
        session_slot_id: str,
        session_id: str,
        execution_client: ExecutionClient,
        on_lot_opened: Optional[OnLotOpenedFn] = None,
        on_lot_closed: Optional[OnLotClosedFn] = None,
        on_state_change: Optional[OnStateFn] = None,
        on_log: Optional[OnLogFn] = None,
        stop_new_entries: bool = False,  # set True for soft-stop
    ):
        self._session_slot_id = session_slot_id
        self._session_id      = session_id
        self._client          = execution_client
        self._on_lot_opened   = on_lot_opened
        self._on_lot_closed   = on_lot_closed
        self._on_state_change = on_state_change
        self._on_log          = on_log

        # Runtime state
        self._running         = False
        self._stop_new_entries = stop_new_entries
        self._market_config: Optional[SlotMarketConfig] = None
        self._lots: list[RuntimeLot] = []
        self._lots_lock       = asyncio.Lock()
        self._loop_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None

        # Slot state
        self._state           = SlotState.IDLE
        self._pending_replacement: Optional[SlotMarketConfig] = None
        self._market_epoch: int = 0

        # Capital
        self._allocated_capital: float = 0.0

        # Aggregate stats
        self._volume:        float = 0.0
        self._realized_pnl:  float = 0.0
        self._unrealized_pnl: float = 0.0
        self._last_mid_price: float = 0.0
        self._last_bid_price: float = 0.0
        self._paid_fees:     float = 0.0
        self._trade_count:   int   = 0   # closed round-trip trades
        self._order_count:   int   = 0   # every buy fill + sell fill (incl. partials)

        # ── Internal tracking (adapted directly from mono_user_bot) ──────────
        # price → order_id for live BUY orders
        self._buy_orders: dict[float, str] = {}
        # levels being bought (in-flight tasks)
        self._buying: Set[float] = set()
        # lot_id → in-flight taker sell
        self._selling: Set[str] = set()
        # lot_ids currently running _post_tp_maker
        self._tp_posting: Set[str] = set()
        # lot_id → monotonic time of last sell failure
        self._sell_fail_time: dict[str, float] = {}
        # order_id → USDC reserved for placed maker buy
        self._pending_buy_capital: dict[str, float] = {}
        # lot_id → sell_order_id for tracked TP maker sells
        self._sell_orders: dict[str, str] = {}
        # order_id → cumulative USDC matched (partial BUY fill tracking)
        self._buy_size_matched: dict[str, float] = {}
        # order_id → cumulative tokens matched (partial TP SELL fill tracking)
        self._sell_size_matched: dict[str, float] = {}
        # level → monotonic_time (per-level BUY failure cooldown)
        self._buy_fail_time: dict[float, float] = {}
        # level → consecutive failure count (detects persistent 500s = market closed)
        self._buy_fail_count: dict[float, int] = {}
        # monotonic time of last cancel (CLOB settle delay)
        self._last_cancel_time: float = 0.0
        # lot_id → monotonic time of last _post_tp_maker attempt
        self._tp_last_attempt: dict[str, float] = {}
        # order_ids for which cancel has been sent but poll hasn't confirmed yet
        self._cancel_sent: Set[str] = set()
        # token_id → accumulated fractional tokens from floor() rounding on sells
        self._dust_balance: dict[str, float] = {}
        # last time we reconciled lot state vs actual poly balance (for stuck replacement)
        self._last_reconcile_time: float = 0.0
        # monotonic time when replacement was queued (for forced timeout)
        self._replacement_queued_at: float = 0.0

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def state(self) -> SlotState:
        return self._state

    @property
    def allocated_capital(self) -> float:
        return self._allocated_capital

    @property
    def open_lots(self) -> list[RuntimeLot]:
        return [l for l in self._lots if l.status in (LotStatus.OPEN, LotStatus.SELLING)]

    @property
    def is_clean(self) -> bool:
        """True when no pending buys, no open lots, no in-flight selling."""
        return (
            not self._buy_orders
            and not self._pending_buy_capital
            and not self.open_lots
            and not self._selling
        )

    @property
    def deployed_capital(self) -> float:
        return sum(l.size for l in self.open_lots)

    @property
    def stats(self) -> dict:
        mc = self._market_config
        open_order_list = [
            {
                "order_id": oid,
                "price": price,
                "size": self._pending_buy_capital.get(oid, self._market_config.lot_size if self._market_config else 0.0),
                "side": "BUY",
            }
            for price, oid in self._buy_orders.items()
        ]
        # TP maker sell orders: keyed lot_id → sell_order_id
        sell_order_list = []
        for l in self.open_lots:
            sell_oid = self._sell_orders.get(l.lot_id)
            if sell_oid:
                sell_order_list.append({
                    "order_id": sell_oid,
                    "price": l.target_sell_price,
                    "size": l.size,
                    "tokens": l.tokens,
                    "side": "SELL",
                    "lot_id": l.lot_id,
                })
        lot_list = [
            {
                "lot_id": l.lot_id,
                "entry_price": l.entry_price,
                "target_sell_price": l.target_sell_price,
                "current_price": self._last_mid_price,
                "size": l.size,
                "tokens": l.tokens,
                "status": l.status.value if hasattr(l.status, "value") else l.status,
                "pnl": getattr(l, "pnl", 0.0),
                "timestamp_entry": l.timestamp_entry,
            }
            for l in self.open_lots
        ]
        return {
            "session_slot_id": self._session_slot_id,
            "state": self._state.value if hasattr(self._state, 'value') else self._state,
            "allocated_capital": self._allocated_capital,
            "deployed_capital": self.deployed_capital,
            "open_lots": len(self.open_lots),
            "volume": self._volume,
            "realized_pnl": self._realized_pnl,
            "unrealized_pnl": self._unrealized_pnl,
            "paid_fees": self._paid_fees,
            "pending_buy_capital": sum(self._pending_buy_capital.values()),
            "trade_count": self._trade_count,
            "order_count": self._order_count,
            # Market config fields for frontend display
            "question": mc.question if mc else None,
            "condition_id": mc.condition_id if mc else None,
            "active_token_id": mc.active_token_id if mc else None,
            "polymarket_url": mc.polymarket_url if mc else None,
            "side": mc.side.value if mc and hasattr(mc.side, "value") else (mc.side if mc else None),
            "price_min": mc.price_min if mc else None,
            "price_max": mc.price_max if mc else None,
            "lot_size": mc.lot_size if mc else None,
            "pending_question": self._pending_replacement.question if self._pending_replacement else None,
            # Live order/position detail
            "open_order_list": open_order_list,
            "sell_order_list": sell_order_list,
            "lot_list": lot_list,
            "bid_price": self._last_bid_price,
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, config: SlotMarketConfig, allocated_capital: float):
        if self._running:
            return
        self._market_config = config
        self._allocated_capital = allocated_capital
        self._running = True
        await self._set_state(SlotState.ACTIVE, "started")
        self._loop_task = asyncio.create_task(
            self._market_loop(), name=f"slot_{self._session_slot_id[:8]}"
        )
        self._poll_task = asyncio.create_task(
            self._poll_maker_fills_loop(), name=f"poll_{self._session_slot_id[:8]}"
        )

    async def stop(self, reason: str = "stopped"):
        self._running = False
        self._stop_new_entries = True
        # Force-sweep any accumulated dust before stopping
        old_token = self._market_config.active_token_id if self._market_config else None
        if old_token:
            await self._sweep_dust(old_token, force=True)
        if self._loop_task:
            self._loop_task.cancel()
        if self._poll_task:
            self._poll_task.cancel()
        await self._set_state(SlotState.IDLE, reason)

    async def _on_market_resolved(self):
        """Market orderbook gone. If replacement queued → switch immediately. Else → IDLE."""
        self._running = False
        self._stop_new_entries = True
        # Stop poll loop — no more fills/sells on a resolved market
        if self._poll_task:
            self._poll_task.cancel()
        if self._pending_replacement:
            await self._log("INFO", f"[MARKET] Resolved — switching to queued replacement")
            await self._execute_switch()
        else:
            await self._log("WARN", f"[MARKET] Resolved — no replacement queued, slot idle (needs force-replace)")
            await self._set_state(SlotState.IDLE, "market resolved")

    async def _reconcile_lots_vs_poly(self):
        """
        When waiting for replacement, check actual token balance on Poly.
        If positions were externally closed (nuclear close, etc.) but we still track
        them as OPEN, force-close them so replacement can proceed.
        """
        m = self._market_config
        if not m or not m.active_token_id:
            return
        try:
            actual = await self._client.get_token_balance(m.active_token_id)
            open_tokens = sum(l.tokens for l in self.open_lots)
            if open_tokens > 0 and actual < 0.5:
                await self._log(
                    "WARN",
                    f"[RECONCILE] Poly balance={actual:.3f} but tracking {open_tokens:.3f}tok open "
                    f"— externally closed. Force-clearing {len(self.open_lots)} lots."
                )
                async with self._lots_lock:
                    for l in self._lots:
                        if l.status in (LotStatus.OPEN, LotStatus.SELLING):
                            l.status = LotStatus.CLOSED
                self._sell_orders.clear()
                self._selling.clear()
        except Exception as e:
            await self._log("WARN", f"[RECONCILE] Balance check failed: {e}")

    async def soft_stop(self):
        """Stop new entries; cancel live buy orders; let open positions exit cleanly."""
        self._stop_new_entries = True
        await self._cancel_all_buys()
        await self._log("INFO", "Soft stop — no new entries, letting positions exit")

    async def sweep_all_dust(self):
        """Force-sweep all accumulated dust tokens. Called when session fully stops."""
        for token_id in list(self._dust_balance.keys()):
            await self._sweep_dust(token_id, force=True)

    async def hard_stop(self):
        """Cancel buys + taker-unwind all open lots immediately."""
        self._stop_new_entries = True
        old_token = self._market_config.active_token_id if self._market_config else None
        if old_token:
            await self._sweep_dust(old_token, force=True)
        await self._cancel_all_buys()

        # Cancel all active TP maker sell orders so tokens are free for taker sell
        for lot_id, sell_oid in list(self._sell_orders.items()):
            try:
                await self._client.cancel_order(sell_oid)
            except Exception as e:
                await self._log("WARN", f"[HARD_STOP] Cancel TP sell {sell_oid[:14]}… failed: {e}")
        self._sell_orders.clear()

        # Fetch book once, sell all lots at bid
        m = self._market_config
        if not m:
            return
        try:
            book = await market_data_service.get_order_book(m.active_token_id)
            bid = norm(float(book["bids"][0]["price"])) if book.get("bids") else None
        except Exception:
            bid = None

        if not bid:
            await self._log("WARN", "[HARD_STOP] No bid available — cannot taker-sell")
            return

        for lot in list(self.open_lots):
            if lot.lot_id not in self._selling:
                asyncio.create_task(self._execute_sell(lot, bid, "TAKER_HARD_STOP"))

    async def force_replace(self, new_config: Optional[SlotMarketConfig]):
        """
        Force-replace: cancel ALL open orders on Poly for this token (tracked or not),
        taker-sell actual token balance on Poly (not just tracked lots), then queue replacement.
        """
        self._stop_new_entries = True
        self._market_epoch += 1
        m = self._market_config
        if not m or not m.active_token_id:
            # Nothing running, just queue then switch immediately
            if new_config is not None:
                self._pending_replacement = new_config
            await self._set_state(SlotState.REPLACEMENT_QUEUED, "force-replace by admin (no market)")
            if not self._running and self.is_clean:
                await self._execute_switch()
            return

        token_id = m.active_token_id
        await self._log("INFO", f"[FORCE_REPLACE] Starting on token {token_id[:12]}…")

        # 1. Cancel ALL open orders on Poly for this token (tracked + untracked)
        try:
            all_orders = await self._client.get_open_orders()
            cancelled = 0
            for oid, od in all_orders.items():
                if od.get("asset_id") == token_id:
                    try:
                        await self._client.cancel_order(oid)
                        cancelled += 1
                    except Exception as e:
                        await self._log("WARN", f"[FORCE_REPLACE] Cancel {oid[:14]}… failed: {e}")
            await self._log("INFO", f"[FORCE_REPLACE] Cancelled {cancelled} order(s) on Poly")
        except Exception as e:
            await self._log("WARN", f"[FORCE_REPLACE] get_open_orders failed: {e}")

        # Clear tracked state
        self._buy_orders.clear()
        self._sell_orders.clear()
        self._pending_buy_capital.clear()
        self._buying.clear()
        self._selling.clear()
        self._cancel_sent.clear()
        self._last_cancel_time = time.monotonic()

        # 2. Get actual token balance and taker-sell everything
        try:
            actual_tokens = await self._client.get_token_balance(token_id)
        except Exception as e:
            await self._log("WARN", f"[FORCE_REPLACE] token balance failed: {e}")
            actual_tokens = None

        sell_tokens = float(math.floor(actual_tokens)) if actual_tokens else 0.0

        if sell_tokens >= 5:
            try:
                book = await market_data_service.get_order_book(token_id)
                bid = norm(float(book["bids"][0]["price"])) if book.get("bids") else None
            except Exception:
                bid = None

            if bid:
                await self._log("INFO", f"[FORCE_REPLACE] Taker-selling {sell_tokens:.0f} tok @ {h(bid)}")
                await self._client.ensure_sell_allowance(token_id)
                try:
                    resp = await self._client.place_limit_order(
                        token_id=token_id, price=bid, size=sell_tokens,
                        side="SELL", tick_size=m.tick_size or "0.001", neg_risk=m.neg_risk,
                    )
                    await self._log("INFO", f"[FORCE_REPLACE] Sell order placed: {resp.get('orderID', '?')[:14]}…")
                except Exception as e:
                    await self._log("WARN", f"[FORCE_REPLACE] Taker-sell failed: {e}")
            else:
                await self._log("WARN", "[FORCE_REPLACE] No bid available — skipping taker-sell")
        elif actual_tokens is not None and actual_tokens > 0:
            await self._log("INFO", f"[FORCE_REPLACE] Actual balance={actual_tokens:.3f} tok < 5 — writing off as dust")
        else:
            await self._log("INFO", "[FORCE_REPLACE] No token balance on Poly — nothing to sell")

        # Force-close all tracked lots so is_clean becomes True quickly
        async with self._lots_lock:
            for l in self._lots:
                if l.status in (LotStatus.OPEN, LotStatus.SELLING):
                    l.status = LotStatus.CLOSED

        # 3. Queue replacement — keep existing _pending_replacement if new_config not provided
        if new_config is not None:
            self._pending_replacement = new_config
        effective = self._pending_replacement
        await self._set_state(SlotState.REPLACEMENT_QUEUED, "force-replace by admin")
        self._replacement_queued_at = time.monotonic()
        label = (effective.question or effective.polymarket_url) if effective else "idle (archived)"
        await self._log("INFO", f"[FORCE_REPLACE] Done — replacement queued → {label}")
        # Lots force-closed above → is_clean is True, switch immediately
        if self.is_clean:
            await self._execute_switch()

    async def queue_replacement(self, new_config: Optional[SlotMarketConfig]):
        """
        Queue a slot market replacement.
        Follows the replacement lifecycle:
          replacement_queued → waiting_previous_clean → switching → active
        None config = slot archived, slot will idle after current positions exit.
        """
        self._pending_replacement = new_config
        self._market_epoch += 1
        self._stop_new_entries = True
        await self._set_state(SlotState.REPLACEMENT_QUEUED, "replacement queued by admin")
        self._replacement_queued_at = time.monotonic()
        await self._cancel_all_buys()
        # Force-clear buy tracking immediately — cancels sent, don't wait for CLOB ACK
        self._buy_orders.clear()
        self._pending_buy_capital.clear()
        self._buy_size_matched.clear()
        self._cancel_sent.clear()
        label = (new_config.question or new_config.polymarket_url) if new_config else "idle (archived)"
        await self._log("INFO", f"Slot replacement queued → {label}")
        # If no loop running OR already clean (no open lots), switch immediately
        if not self._running or self.is_clean:
            await self._execute_switch()

    def set_allocated_capital(self, capital: float):
        self._allocated_capital = capital
        if self._market_config and capital > 0:
            levels = self._market_config.max_spread_ticks + 1
            if levels > 0:
                self._market_config.lot_size = round(capital / levels, 4)

    def update_paid_fees(self, amount: float):
        self._paid_fees += amount

    # ── State machine ─────────────────────────────────────────────────────────

    async def _set_state(self, state: SlotState, reason: str = ""):
        self._state = state
        if self._on_state_change:
            try:
                await self._on_state_change(state, reason)
            except Exception:
                pass

    # ── Logging ───────────────────────────────────────────────────────────────

    async def _log(self, level: str, msg: str):
        if self._on_log:
            try:
                await self._on_log(level, f"[slot:{self._session_slot_id[:8]}] {msg}")
            except Exception:
                pass

    # ── Dust recovery ─────────────────────────────────────────────────────────

    async def _account_dust(self, token_id: str, frac: float) -> None:
        """Accumulate fractional tokens cut off by math.floor() on sells."""
        if frac < 0.001:
            return
        self._dust_balance[token_id] = self._dust_balance.get(token_id, 0.0) + frac
        if math.floor(self._dust_balance[token_id]) >= 5:  # Poly min order = 5 tokens
            await self._sweep_dust(token_id)

    async def _sweep_dust(self, token_id: str, force: bool = False) -> None:
        """Sell accumulated dust tokens. Normal: requires ≥5 tokens. Force: requires ≥1 (on slot exit)."""
        total = self._dust_balance.get(token_id, 0.0)
        sweep_tokens = float(math.floor(total))
        if not force and sweep_tokens < 5:
            return
        if sweep_tokens < 1:
            return
        m = self._market_config
        if not m:
            return
        try:
            book = await market_data_service.get_order_book(token_id)
            bid = norm(float(book["bids"][0]["price"])) if book.get("bids") else None
            if not bid:
                return
            await self._client.ensure_sell_allowance(token_id)
            await self._client.place_limit_order(
                token_id=token_id, price=bid, size=sweep_tokens,
                side="SELL", tick_size=m.tick_size or "0.001", neg_risk=m.neg_risk,
            )
            self._dust_balance[token_id] = total - sweep_tokens
            await self._log("INFO", f"[DUST] Swept {sweep_tokens:.0f}tok @ {h(bid)} (rem={total - sweep_tokens:.3f})")
        except Exception as e:
            await self._log("WARN", f"[DUST] Sweep failed: {e}")

    # ── Cancel all buy orders ─────────────────────────────────────────────────

    async def _cancel_all_buys(self):
        for price, order_id in list(self._buy_orders.items()):
            if order_id and order_id not in self._cancel_sent:
                try:
                    await self._client.cancel_order(order_id)
                    self._cancel_sent.add(order_id)
                except Exception as e:
                    await self._log("WARN", f"Cancel buy @ {h(price)} failed: {e}")
        self._last_cancel_time = time.monotonic()

    # ── Main market loop ──────────────────────────────────────────────────────

    async def _market_loop(self):
        try:
            m = self._market_config
            if not m or not m.active_token_id:
                await self._set_state(SlotState.ERROR, "no active token")
                return

            tick   = real_tick(m.tick_size)
            _cycle = 0
            _last_bid = None

            await self._log(
                "SUCCESS",
                f"Grid MM: {(m.question or m.polymarket_url)[:50]} | "
                f"{h(m.price_min)}–{h(m.price_max)} | tick={tick} | "
                f"lot=${m.lot_size:.2f} | levels={m.max_spread_ticks + 1}",
            )

            while self._running:
                m = self._market_config
                if not m:
                    break

                _cycle += 1

                try:
                    book = await market_data_service.get_order_book(m.active_token_id)
                except MarketClosedError as e:
                    await self._log("INFO", f"[MARKET] {e} — market resolved")
                    await self._on_market_resolved()
                    return
                except Exception:
                    await asyncio.sleep(POLL_S)
                    continue

                bid = book["bids"][0]["price"] if book.get("bids") else None
                ask = book["asks"][0]["price"] if book.get("asks") else None
                if not bid or not ask:
                    await asyncio.sleep(POLL_S)
                    continue

                bid  = norm(bid)
                ask  = norm(ask)
                tick = real_tick(m.tick_size)

                # Update unrealized PnL
                mid_price = norm((bid + ask) / 2)
                self._update_unrealized(mid_price)

                anchor = norm(ask - tick) if m.spread_from_ask else bid
                levels = grid_levels(anchor, tick, m.max_spread_ticks, m.price_min, m.price_max)

                bid_moved   = (_last_bid is not None and bid != _last_bid)
                _last_bid   = bid
                self._last_bid_price = bid

                # 1. Rebalance: cancel stale buys when grid shifts
                if bid_moved or m.spread_from_ask:
                    await self._rebalance(m, levels)

                # 2. SL check
                await self._check_sl(m, bid, tick)

                # 3. TP taker fallback
                await self._check_tp(m, bid)

                # 4. Fill grid (skip if soft/hard stopping or replacement queued)
                if not self._stop_new_entries:
                    await self._fill_grid(m, bid, tick, levels)

                # 5. Check if replacement ready to switch
                if self._state in (SlotState.WAITING_PREVIOUS_CLEAN, SlotState.REPLACEMENT_QUEUED):
                    now_t = time.monotonic()
                    # Reconcile lots vs actual poly balance every 60s
                    if self.open_lots and now_t - self._last_reconcile_time > 60.0:
                        self._last_reconcile_time = now_t
                        await self._reconcile_lots_vs_poly()
                    # Force-switch after 3min regardless — prevents infinite stuck on CLOB failures
                    if self._replacement_queued_at > 0 and now_t - self._replacement_queued_at > 180.0:
                        await self._log("WARN", f"[REPLACE] Timeout 3min — force-closing {len(self.open_lots)} lots and switching")
                        async with self._lots_lock:
                            for l in self._lots:
                                if l.status in (LotStatus.OPEN, LotStatus.SELLING):
                                    l.status = LotStatus.CLOSED
                        self._sell_orders.clear()
                        self._selling.clear()
                        self._replacement_queued_at = 0.0
                        await self._execute_switch()
                        continue

                if self._state == SlotState.WAITING_PREVIOUS_CLEAN and self.is_clean:
                    await self._execute_switch()

                elif self._state == SlotState.REPLACEMENT_QUEUED and self.is_clean:
                    # All buys cancelled; now wait for open lots to exit
                    if self.open_lots:
                        await self._set_state(SlotState.WAITING_PREVIOUS_CLEAN, "waiting for lots to exit")
                    else:
                        await self._execute_switch()

                await asyncio.sleep(POLL_S)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            import traceback
            await self._log("ERROR", f"Loop crash: {e}\n{traceback.format_exc()[-300:]}")
            await self._set_state(SlotState.ERROR, str(e))

    async def _execute_switch(self):
        """Switch to the pending replacement market."""
        new_config = self._pending_replacement
        if not new_config:
            await self._set_state(SlotState.IDLE, "no replacement config")
            return
        await self._set_state(SlotState.SWITCHING, "switching market")

        # Force-sweep dust for outgoing token before switching
        old_token = self._market_config.active_token_id if self._market_config else None
        if old_token:
            await self._sweep_dust(old_token, force=True)

        # Reset runtime tracking state for the new market
        self._buy_orders.clear()
        self._buying.clear()
        self._sell_orders.clear()
        self._pending_buy_capital.clear()
        self._buy_size_matched.clear()
        self._sell_size_matched.clear()
        self._buy_fail_time.clear()
        self._buy_fail_count.clear()
        self._cancel_sent.clear()
        self._last_cancel_time = 0.0

        self._market_config = new_config
        self._market_epoch += 1
        self._pending_replacement = None
        self._stop_new_entries = False
        self._lots = [l for l in self._lots if l.status == LotStatus.CLOSED]

        await self._set_state(SlotState.ACTIVE, f"switched to {new_config.question or new_config.polymarket_url}")
        await self._log("SUCCESS", f"Slot switched → {(new_config.question or new_config.polymarket_url)[:60]}")

        # If the loop wasn't running (slot was IDLE), start it now
        if not self._running:
            self._running = True
            self._loop_task = asyncio.create_task(
                self._market_loop(), name=f"slot_{self._session_slot_id[:8]}"
            )
            self._poll_task = asyncio.create_task(
                self._poll_maker_fills_loop(), name=f"poll_{self._session_slot_id[:8]}"
            )

    # ── Rebalance ─────────────────────────────────────────────────────────────

    async def _rebalance(self, m: SlotMarketConfig, valid_levels: list[float]):
        """
        Cancel BUY orders outside the valid grid.
        Does NOT pop from _buy_orders — lets _poll_maker_fills do cleanup.
        Adapted from mono_user_bot TradingEngine._rebalance().
        """
        valid_set = set(valid_levels)
        stale = {p for p in self._buy_orders if p not in valid_set}
        if not stale:
            return

        cancelled = []
        for price in stale:
            order_id = self._buy_orders.get(price)
            if not order_id or order_id in self._cancel_sent:
                continue
            try:
                await self._client.cancel_order(order_id)
                self._cancel_sent.add(order_id)
                cancelled.append(h(price))
            except Exception as e:
                await self._log("WARN", f"[GRID] Cancel @ {h(price)} failed: {e}")

        if cancelled:
            self._last_cancel_time = time.monotonic()
            await self._log("INFO", f"[GRID] Rebalanced: cancelled={cancelled}")

    # ── TP check (taker fallback) ─────────────────────────────────────────────

    async def _check_tp(self, m: SlotMarketConfig, bid: float):
        now = time.monotonic()
        for lot in self.open_lots:
            if lot.lot_id in self._selling:
                continue
            if lot.lot_id in self._sell_orders:
                continue
            fail_t = self._sell_fail_time.get(lot.lot_id, 0.0)
            if now - fail_t < 30.0:
                continue
            if bid >= lot.target_sell_price:
                await self._log(
                    "SUCCESS",
                    f"TP HIT lot={lot.lot_id[:8]} entry={h(lot.entry_price)} "
                    f"tp={h(lot.target_sell_price)} bid={h(bid)} → taker",
                )
                asyncio.create_task(self._execute_sell(lot, bid, "TAKER"))

    # ── SL check ─────────────────────────────────────────────────────────────

    async def _check_sl(self, m: SlotMarketConfig, bid: float, tick: float):
        # SL only triggers on sl_ticks below entry price. price_min is grid range only, not SL.
        if m.sl_ticks <= 0:
            return  # no SL configured
        now = time.monotonic()
        for lot in self.open_lots:
            if lot.lot_id in self._selling:
                continue
            if now - self._sell_fail_time.get(lot.lot_id, 0.0) < 30.0:
                continue
            sl = norm(lot.entry_price - m.sl_ticks * tick)
            if bid <= sl:
                await self._log(
                    "WARN",
                    f"SL HIT lot={lot.lot_id[:8]} entry={h(lot.entry_price)} "
                    f"bid={h(bid)} sl={h(sl)}",
                )
                tp_oid = self._sell_orders.pop(lot.lot_id, None)
                if tp_oid:
                    try:
                        await self._client.cancel_order(tp_oid)
                    except Exception as e:
                        await self._log("WARN", f"[SL] Cancel TP sell failed: {e}")
                asyncio.create_task(self._execute_sell(lot, bid, "TAKER_SL"))

    # ── Execute sell (TAKER) ──────────────────────────────────────────────────

    async def _execute_sell(self, lot: RuntimeLot, price: float, mode: str):
        if lot.lot_id in self._selling:
            return
        self._selling.add(lot.lot_id)

        m = self._market_config
        if not m:
            self._selling.discard(lot.lot_id)
            return

        async with self._lots_lock:
            for l in self._lots:
                if l.lot_id == lot.lot_id:
                    l.status = LotStatus.SELLING
                    break

        try:
            # Cancel any conflicting buy at same price
            conflict_oid = self._buy_orders.get(price)
            if conflict_oid:
                try:
                    await self._client.cancel_order(conflict_oid)
                    self._buy_orders.pop(price, None)
                    self._pending_buy_capital.pop(conflict_oid, None)
                    self._buy_size_matched.pop(conflict_oid, None)
                except Exception:
                    pass

            order_id, actual, dust_frac = await self._place_sell(m, lot, price)
            if order_id is None:
                if math.floor(lot.tokens) < 5:
                    # Dust lot (< 5 tokens) — close so it stops triggering every tick
                    await self._account_dust(m.active_token_id, lot.tokens)
                    await self._close_lot(lot.lot_id, lot.entry_price, "dust")
                    await self._log("WARN", f"[SELL] Dust lot closed lot={lot.lot_id[:8]} tokens={lot.tokens:.3f}")
                else:
                    async with self._lots_lock:
                        for l in self._lots:
                            if l.lot_id == lot.lot_id:
                                l.status = LotStatus.OPEN
                                break
                return

            pnl = await self._close_lot(lot.lot_id, actual, order_id)
            if pnl is not None:
                self._volume += round(math.floor(lot.tokens) * actual, 6)  # sell side
                await self._account_dust(m.active_token_id, dust_frac)
                self._sell_fail_time.pop(lot.lot_id, None)
                self._tp_last_attempt.pop(lot.lot_id, None)
                pct  = (actual - lot.entry_price) / lot.entry_price * 100
                sign = "+" if pnl >= 0 else ""
                await self._log(
                    "SUCCESS",
                    f"✓ SOLD @ {h(actual)} | PnL {sign}${pnl:.4f} ({sign}{pct:.1f}%) | "
                    f"entry={h(lot.entry_price)} lot={lot.lot_id[:8]}",
                )
                if self._on_lot_closed:
                    await self._on_lot_closed(lot, pnl)

        except Exception as e:
            err_str = str(e).lower()
            if "not enough balance" in err_str or "balance is not enough" in err_str:
                # Phantom close — maker TP filled undetected
                pnl = await self._close_lot(lot.lot_id, lot.target_sell_price, "phantom")
                if pnl is not None:
                    self._sell_fail_time.pop(lot.lot_id, None)
                    self._tp_last_attempt.pop(lot.lot_id, None)
                    pct  = (lot.target_sell_price - lot.entry_price) / lot.entry_price * 100
                    sign = "+" if pnl >= 0 else ""
                    await self._log(
                        "WARN",
                        f"[SELL] Phantom close lot={lot.lot_id[:8]} "
                        f"entry={h(lot.entry_price)} tp={h(lot.target_sell_price)} ({sign}{pct:.1f}%)",
                    )
                    if self._on_lot_closed and pnl is not None:
                        await self._on_lot_closed(lot, pnl)
            else:
                await self._log("ERROR", f"Sell failed lot={lot.lot_id[:8]}: {e}")
                self._sell_fail_time[lot.lot_id] = time.monotonic()
                async with self._lots_lock:
                    for l in self._lots:
                        if l.lot_id == lot.lot_id:
                            l.status = LotStatus.OPEN
                            break
        finally:
            self._selling.discard(lot.lot_id)

    # ── Fill grid ─────────────────────────────────────────────────────────────

    async def _fill_grid(self, m: SlotMarketConfig, bid: float, tick: float, levels: list[float]):
        filled_at = {norm(l.entry_price) for l in self.open_lots}

        # Wait for CLOB to settle cancelled capital
        if time.monotonic() - self._last_cancel_time < POST_CANCEL_SETTLE:
            return

        wallet_usdc      = await self._client.get_cached_usdc()
        open_lot_capital = sum(l.size for l in self.open_lots)
        pending_capital  = sum(self._pending_buy_capital.values())
        deployable       = min(
            wallet_usdc,
            max(0.0, self._allocated_capital - open_lot_capital - pending_capital)
        )

        now = time.monotonic()

        for level in levels:
            if level in self._buy_orders or level in self._buying:
                continue
            if now - self._buy_fail_time.get(level, 0) < BUY_FAIL_COOLDOWN:
                continue
            if level in filled_at:
                continue
            if deployable < POLY_MIN_ORDER_USDC:
                continue

            asyncio.create_task(self._execute_buy(level, tick))

    async def _execute_buy(self, level: float, tick: float):
        m = self._market_config
        if not m:
            return
        epoch = self._market_epoch
        token_id = m.active_token_id
        if level in self._buying:
            return
        self._buying.add(level)

        wallet_usdc      = await self._client.get_cached_usdc()
        open_lot_capital = sum(l.size for l in self.open_lots)
        pending_capital  = sum(self._pending_buy_capital.values())
        deployable       = min(
            wallet_usdc,
            max(0.0, self._allocated_capital - open_lot_capital - pending_capital)
        )
        size = min(m.lot_size, deployable)
        if size < POLY_MIN_ORDER_USDC:
            self._buying.discard(level)
            return
        if (
            self._market_epoch != epoch
            or self._pending_replacement is not None
            or self._stop_new_entries
            or not self._market_config
            or self._market_config.active_token_id != token_id
        ):
            self._buying.discard(level)
            return

        try:
            order_id, filled_price, is_matched, tokens_filled = await self._place_buy(m, level, size)
            if order_id is None:
                return
            if (
                self._market_epoch != epoch
                or not self._market_config
                or self._market_config.active_token_id != token_id
            ):
                self._buy_orders.pop(level, None)
                self._pending_buy_capital.pop(order_id, None)
                self._buy_size_matched.pop(order_id, None)
                self._cancel_sent.discard(order_id)
                try:
                    await self._client.cancel_order(order_id)
                except Exception:
                    pass
                return

            self._buy_fail_time.pop(level, None)
            self._buy_fail_count.pop(level, None)

            if not is_matched:
                self._pending_buy_capital[order_id] = size
                await self._log("INFO", f"[BUY] Maker @ {h(level)} ×${size:.0f} | {order_id[:14]}…")
                return

            # Taker fill — create lot immediately
            tp_price = norm(filled_price + tick)
            tokens   = round(tokens_filled, 6) if tokens_filled > 0 else round(size / filled_price, 6)
            lot = RuntimeLot(
                session_slot_id=self._session_slot_id,
                entry_price=norm(filled_price),
                target_sell_price=tp_price,
                size=size,
                tokens=tokens,
                entry_order_id=order_id,
                status=LotStatus.OPEN,
                timestamp_entry=datetime.now(timezone.utc).isoformat(),
            )
            async with self._lots_lock:
                self._lots.append(lot)
            self._volume += size  # buy side
            self._order_count += 1   # taker buy filled
            await self._log(
                "SUCCESS",
                f"✓ BUY (taker) {tokens:.2f}tok @ {h(filled_price)} | TP={h(tp_price)} | lot={lot.lot_id[:8]}",
            )
            if self._on_lot_opened:
                await self._on_lot_opened(lot)
            asyncio.create_task(self._post_tp_maker(lot))

        except Exception as e:
            err_str = str(e).lower()
            permanent = any(x in err_str for x in (
                "lower than the minimum", "minimum", "invalid size",
                "invalid price", "invalid params", "market closed", "market not found",
                "not available for trading", "market is not active", "not_available",
                "trading is closed", "market resolved",
            ))
            self._buy_fail_count[level] = self._buy_fail_count.get(level, 0) + 1
            # 500 errors are transient server errors — use long cooldown, not permanent
            if not permanent and "500" in err_str:
                await self._log("WARN", f"[BUY] 500 @ {h(level)} (#{self._buy_fail_count[level]}) — 2m cooldown")
                self._buy_fail_time[level] = time.monotonic() + 120.0
            elif permanent:
                await self._log("ERROR", f"[BUY] Permanent error @ {h(level)}: {e} — slot to error")
                await self._set_state(SlotState.ERROR, str(e)[:120])
            elif "does not exist" in err_str and "orderbook" in err_str:
                await self._log("INFO", f"[BUY] Orderbook gone @ {h(level)} — market resolved")
                await self._on_market_resolved()
                return
            elif "not enough balance" in err_str or "balance is not enough" in err_str:
                await self._log("WARN", f"[BUY] Balance unavailable @ {h(level)} — retry in {BUY_FAIL_COOLDOWN:.0f}s")
                self._buy_fail_time[level] = time.monotonic()
            else:
                await self._log("WARN", f"[BUY] Failed @ {h(level)}: {e}")
                self._buy_fail_time[level] = time.monotonic()
        finally:
            self._buying.discard(level)

    # ── Order placement ───────────────────────────────────────────────────────

    async def _place_buy(self, m: SlotMarketConfig, price: float, size: float) -> tuple:
        """size = USDC amount; converts to tokens for CLOB API."""
        size_tokens = round(size / price, 6)
        resp = await self._client.place_limit_order(
            token_id=m.active_token_id,
            price=price,
            size=size_tokens,
            side="BUY",
            tick_size=m.tick_size or "0.001",
            neg_risk=m.neg_risk,
            slot_label=(m.question or "")[:30],
        )
        order_id = resp.get("orderID") or resp.get("order_id") or "?"
        status   = resp.get("status", "")

        self._buy_orders[price] = order_id

        tokens_filled = 0.0
        if status == "matched":
            try:
                taking = float(resp.get("takingAmount", 0))
                making = float(resp.get("makingAmount", 0))
                actual = norm(making / taking) if taking > 0 else price
                tokens_filled = taking
            except (ValueError, ZeroDivisionError):
                actual = price
            self._buy_orders.pop(price, None)
        else:
            actual = price

        return order_id, actual, (status == "matched"), tokens_filled

    async def _place_sell(self, m: SlotMarketConfig, lot: RuntimeLot, price: float) -> tuple:
        # Returns (order_id, actual_price, dust_frac)
        token_size = float(math.floor(lot.tokens))
        dust_frac  = lot.tokens - token_size
        if token_size < 5:  # Polymarket minimum order size is 5 tokens
            await self._log("WARN", f"SELL skipped lot={lot.lot_id[:8]}: tokens={lot.tokens:.3f} < 5 (dust)")
            return None, 0.0, lot.tokens  # full lot is dust

        await self._client.ensure_sell_allowance(m.active_token_id)
        resp = await self._client.place_limit_order(
            token_id=m.active_token_id,
            price=price,
            size=token_size,
            side="SELL",
            tick_size=m.tick_size or "0.001",
            neg_risk=m.neg_risk,
            slot_label=(m.question or "")[:30],
        )
        order_id = resp.get("orderID") or resp.get("order_id") or "?"
        return order_id, norm(price), dust_frac

    # ── TP Maker post ─────────────────────────────────────────────────────────

    async def _post_tp_maker(self, lot: RuntimeLot):
        if lot.lot_id in self._tp_posting:
            return
        self._tp_posting.add(lot.lot_id)
        try:
            m = self._market_config
            if not m:
                return
            # Check lot still open
            still_open = any(l.lot_id == lot.lot_id and l.open() for l in self._lots)
            if not still_open:
                return
            if lot.lot_id in self._sell_orders:
                return

            order_id, _, dust_frac = await self._place_sell(m, lot, lot.target_sell_price)
            if order_id and order_id != "?":
                self._sell_orders[lot.lot_id] = order_id
                await self._account_dust(m.active_token_id, dust_frac)
                await self._log(
                    "INFO",
                    f"[TP] Maker sell posted @ {h(lot.target_sell_price)} lot={lot.lot_id[:8]} | {order_id[:14]}…",
                )
                self._tp_last_attempt[lot.lot_id] = time.monotonic()
            elif order_id is None and lot.tokens < 5.0:
                # Sub-5 token lot — below Poly minimum, write off as dust
                await self._log(
                    "WARN",
                    f"[TP] Dust write-off lot={lot.lot_id[:8]} tokens={lot.tokens:.4f} (<5, unsellable)",
                )
                await self._close_lot(lot.lot_id, lot.entry_price, "dust-writeoff")
        except Exception as e:
            await self._log("WARN", f"[TP] Post failed lot={lot.lot_id[:8]}: {e}")
            self._sell_fail_time[lot.lot_id] = time.monotonic()
        finally:
            self._tp_posting.discard(lot.lot_id)

    # ── Maker fill poll loop ──────────────────────────────────────────────────

    async def _poll_maker_fills_loop(self):
        while self._running:
            await asyncio.sleep(5.0)
            try:
                await self._poll_maker_fills()
            except Exception as e:
                await self._log("WARN", f"[POLL] Fill poll error: {e}")

    async def _poll_maker_fills(self):
        """
        1. Detect BUY partial/full fills → create lots, post TP.
        2. Detect TP SELL fills → close lots.
        3. Re-post TP for open lots without tracked sell (every 30s).

        Uses single bulk get_open_orders() call — anti-spam pattern from mono_user_bot.
        """
        buy_tracked:  dict[str, tuple[float]] = {}  # oid → (price,)
        sell_tracked: dict[str, str] = {}            # oid → lot_id

        for price, oid in list(self._buy_orders.items()):
            if oid and oid != "?":
                buy_tracked[oid] = (price,)
        for lot_id, oid in list(self._sell_orders.items()):
            if oid and oid != "?":
                sell_tracked[oid] = lot_id

        if not buy_tracked and not sell_tracked:
            await self._repost_missing_tp()
            return

        try:
            open_orders = await self._client.get_open_orders()
        except Exception:
            open_orders = {}

        m = self._market_config
        if not m:
            return
        tick = real_tick(m.tick_size)

        # ── BUY orders ────────────────────────────────────────────────────────
        for oid, (price,) in list(buy_tracked.items()):
            if self._buy_orders.get(price) != oid:
                continue

            still_open = oid in open_orders

            if still_open:
                order_data    = open_orders[oid]
                status_raw    = (order_data.get("status") or "").upper()
                try:
                    matched_total = float(
                        order_data.get("size_matched") or order_data.get("matched_amount") or 0
                    )
                except (TypeError, ValueError):
                    matched_total = 0.0
            else:
                order_info    = await self._client.get_order_status(oid)
                status_raw    = (order_info.get("status") or "").upper()
                try:
                    matched_total = float(
                        order_info.get("size_matched") or order_info.get("matched_amount") or 0
                    )
                except (TypeError, ValueError):
                    matched_total = 0.0

                if matched_total == 0.0 and status_raw == "LIVE":
                    await asyncio.sleep(1.5)
                    order_info2  = await self._client.get_order_status(oid)
                    status_raw   = (order_info2.get("status") or "").upper()
                    try:
                        matched_total = float(
                            order_info2.get("size_matched") or order_info2.get("matched_amount") or 0
                        )
                    except (TypeError, ValueError):
                        pass
                    await self._log(
                        "WARN",
                        f"[POLL] BUY {oid[:14]}… LIVE+gone → status={status_raw} matched={matched_total:.4f}",
                    )

            prev_matched = self._buy_size_matched.get(oid, 0.0)
            delta        = round(matched_total - prev_matched, 6)

            if delta >= 0.01:
                self._buy_size_matched[oid] = matched_total
                usdc_cost = round(delta * price, 6)
                self._pending_buy_capital[oid] = max(
                    0.0, self._pending_buy_capital.get(oid, 0.0) - usdc_cost
                )
                tokens   = delta
                tp_price = norm(price + tick)
                lot = RuntimeLot(
                    session_slot_id=self._session_slot_id,
                    entry_price=norm(price),
                    target_sell_price=tp_price,
                    size=usdc_cost,
                    tokens=tokens,
                    entry_order_id=oid,
                    status=LotStatus.OPEN,
                    timestamp_entry=datetime.now(timezone.utc).isoformat(),
                )
                async with self._lots_lock:
                    self._lots.append(lot)
                self._volume += usdc_cost  # buy side
                self._order_count += 1   # maker buy fill (incl. partial)
                label = "PARTIAL" if still_open else "FILL"
                await self._log(
                    "SUCCESS",
                    f"✓ MAKER {label} @ {h(price)} +${usdc_cost:.2f} | "
                    f"{tokens:.3f}tok | lot={lot.lot_id[:8]} TP={h(tp_price)}",
                )
                if self._on_lot_opened:
                    await self._on_lot_opened(lot)
                asyncio.create_task(self._post_tp_maker(lot))

            if not still_open:
                if status_raw == "LIVE":
                    await self._log("WARN", f"[POLL] BUY {oid[:14]}… LIVE but not in open_ids — keeping")
                else:
                    if status_raw and status_raw not in ("MATCHED", "FILLED", ""):
                        await self._log("WARN", f"[POLL] BUY {oid[:14]}… status={status_raw} — discarding")
                    self._buy_orders.pop(price, None)
                    self._pending_buy_capital.pop(oid, None)
                    self._buy_size_matched.pop(oid, None)
                    self._cancel_sent.discard(oid)

        # ── TP SELL orders ────────────────────────────────────────────────────
        for oid, lot_id in list(sell_tracked.items()):
            if self._sell_orders.get(lot_id) != oid:
                continue

            still_open = oid in open_orders

            if still_open:
                order_data = open_orders[oid]
                try:
                    tokens_matched_total = float(
                        order_data.get("size_matched") or order_data.get("matched_amount") or 0
                    )
                except (TypeError, ValueError):
                    tokens_matched_total = 0.0
                status_raw = (order_data.get("status") or "").upper()
            else:
                order_info = await self._client.get_order_status(oid)
                try:
                    tokens_matched_total = float(
                        order_info.get("size_matched") or order_info.get("matched_amount") or 0
                    )
                except (TypeError, ValueError):
                    tokens_matched_total = 0.0
                status_raw = (order_info.get("status") or "").upper()

            prev_matched = self._sell_size_matched.get(oid, 0.0)
            delta_tokens = round(tokens_matched_total - prev_matched, 6)

            # Find the lot
            target_lot = next((l for l in self._lots if l.lot_id == lot_id), None)
            if not target_lot:
                self._sell_orders.pop(lot_id, None)
                continue

            if delta_tokens >= 0.01:
                self._sell_size_matched[oid] = tokens_matched_total
                usdc_returned = round(delta_tokens * target_lot.target_sell_price, 6)

                if delta_tokens >= target_lot.tokens * 0.99:
                    # Full (or near-full) fill — close lot
                    self._volume += usdc_returned  # sell side
                    pnl = await self._close_lot(lot_id, target_lot.target_sell_price, oid)
                    self._sell_orders.pop(lot_id, None)
                    self._sell_size_matched.pop(oid, None)
                    await self._log(
                        "SUCCESS",
                        f"✓ TP SELL filled lot={lot_id[:8]} @ {h(target_lot.target_sell_price)} | "
                        f"PnL ${pnl:.4f}" if pnl is not None else f"✓ TP SELL filled lot={lot_id[:8]}",
                    )
                    if self._on_lot_closed and pnl is not None:
                        await self._on_lot_closed(target_lot, pnl)
                else:
                    # Partial fill — reduce lot tokens and book partial PnL
                    self._volume += usdc_returned  # sell side (partial)
                    partial_pnl = round((target_lot.target_sell_price - target_lot.entry_price) * delta_tokens, 6)
                    self._realized_pnl += partial_pnl
                    self._order_count += 1   # partial sell fill
                    async with self._lots_lock:
                        for l in self._lots:
                            if l.lot_id == lot_id:
                                l.tokens = max(0.0, round(l.tokens - delta_tokens, 6))
                                l.size   = max(0.0, round(l.size - usdc_returned, 6))
                                l.pnl    = round(getattr(l, 'pnl', 0.0) + partial_pnl, 6)
                                break
                    sign = "+" if partial_pnl >= 0 else ""
                    await self._log(
                        "INFO",
                        f"[TP] Partial SELL lot={lot_id[:8]} -{delta_tokens:.3f}tok PnL {sign}${partial_pnl:.4f}",
                    )

            if not still_open and status_raw not in ("LIVE", "MATCHED", "FILLED", ""):
                self._sell_orders.pop(lot_id, None)
                self._sell_size_matched.pop(oid, None)

        await self._repost_missing_tp()

    async def _repost_missing_tp(self):
        """Re-post TP maker sell for open lots without a tracked sell order (rate limited)."""
        now = time.monotonic()
        for lot in self.open_lots:
            if lot.lot_id in self._sell_orders:
                continue
            if lot.lot_id in self._selling:
                continue
            if lot.lot_id in self._tp_posting:
                continue
            last_attempt = self._tp_last_attempt.get(lot.lot_id, 0.0)
            if now - last_attempt < TP_REPOST_INTERVAL:
                continue
            asyncio.create_task(self._post_tp_maker(lot))

    # ── Lot management ────────────────────────────────────────────────────────

    async def _close_lot(self, lot_id: str, sell_price: float, exit_order_id: str) -> Optional[float]:
        async with self._lots_lock:
            for lot in self._lots:
                if lot.lot_id == lot_id and lot.status in (LotStatus.OPEN, LotStatus.SELLING):
                    lot.sell_price     = sell_price
                    lot.exit_order_id  = exit_order_id
                    lot.status         = LotStatus.CLOSED
                    lot.timestamp_exit = datetime.now(timezone.utc).isoformat()
                    lot.pnl            = round((sell_price - lot.entry_price) * lot.tokens, 6)
                    self._realized_pnl += lot.pnl
                    self._trade_count  += 1
                    self._order_count  += 1   # full sell fill
                    return lot.pnl
        return None

    def _update_unrealized(self, mid_price: float):
        self._last_mid_price = mid_price
        upnl = sum(
            (mid_price - l.entry_price) * l.tokens
            for l in self._lots
            if l.status in (LotStatus.OPEN, LotStatus.SELLING)
        )
        self._unrealized_pnl = round(upnl, 6)
