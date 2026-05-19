"""
runtime_models.py — In-memory runtime data structures.

These are NOT ORM models. They live entirely in memory during an active session.
DB models in db/models.py store durable state.
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


PRICE_MAX = 0.999
PRICE_MIN = 0.001


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Enums ─────────────────────────────────────────────────────────────────────

class Side(str, Enum):
    YES = "YES"
    NO  = "NO"


class LotStatus(str, Enum):
    OPEN    = "OPEN"
    SELLING = "SELLING"
    CLOSED  = "CLOSED"


class SlotState(str, Enum):
    ACTIVE                = "active"
    REPLACEMENT_QUEUED    = "replacement_queued"
    WAITING_PREVIOUS_CLEAN = "waiting_previous_clean"
    SWITCHING             = "switching"
    IDLE                  = "idle"
    DISABLED              = "disabled"
    ERROR                 = "error"


class SessionHealthState(str, Enum):
    STARTING   = "starting"
    RUNNING    = "running"
    IDLE       = "idle"
    RECOVERING = "recovering"
    DEGRADED   = "degraded"
    STOPPING   = "stopping"
    STOPPED    = "stopped"
    ERROR      = "error"


class StopMode(str, Enum):
    DAYS   = "days"
    VOLUME = "volume"


# ── Price helpers (adapted from mono_user_bot) ────────────────────────────────

def real_tick(tick_size: str) -> float:
    """
    Real effective tick size.
    Gamma returns 0.01 but actual CLOB tick is 0.001.
    Adapted from mono_user_bot trading_engine.real_tick().
    """
    try:
        t = float(tick_size or "0.001")
        if t <= 0:  return 0.001
        if t >= 0.01: return 0.001  # Gamma tick is coarse; real tick is finer
        return t
    except (ValueError, TypeError):
        return 0.001


def norm(p: float) -> float:
    """Clamp price to [PRICE_MIN, PRICE_MAX] and round to 4dp."""
    return round(max(PRICE_MIN, min(PRICE_MAX, p)), 4)


def h(p: float) -> str:
    return f"{p * 100:.1f}¢"


def grid_levels(
    best_bid: float,
    tick: float,
    spread_ticks: int,
    price_min: float,
    price_max: float,
) -> list[float]:
    """
    Compute grid price levels.
    Identical to mono_user_bot trading_engine.grid_levels().
    """
    levels = []
    for i in range(spread_ticks + 1):
        p = norm(best_bid - i * tick)
        if price_min <= p <= price_max:
            levels.append(p)
    return levels


# ── Runtime Lot ───────────────────────────────────────────────────────────────

class RuntimeLot(BaseModel):
    lot_id:             str   = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    session_slot_id:    str
    entry_price:        float
    target_sell_price:  float
    size:               float   # USDC cost
    tokens:             float   = 0.0
    entry_order_id:     Optional[str] = None
    exit_order_id:      Optional[str] = None
    status:             LotStatus = LotStatus.OPEN
    pnl:                float   = 0.0
    sell_price:         Optional[float] = None
    timestamp_entry:    Optional[str] = None
    timestamp_exit:     Optional[str] = None

    class Config:
        use_enum_values = True

    def open(self) -> bool:
        return self.status in (LotStatus.OPEN, LotStatus.SELLING)


# ── Runtime Slot Market Config ────────────────────────────────────────────────

class SlotMarketConfig(BaseModel):
    """Active market configuration for a slot runtime."""
    polymarket_url:   str
    question:         Optional[str] = None
    condition_id:     Optional[str] = None
    token_id_yes:     Optional[str] = None
    token_id_no:      Optional[str] = None
    active_token_id:  Optional[str] = None
    tick_size:        str = "0.001"
    neg_risk:         bool = False

    side:             Side   = Side.YES
    price_min:        float
    price_max:        float
    max_spread_ticks: int    = 1    # grid levels = max_spread_ticks + 1
    sl_ticks:         int    = 0
    spread_from_ask:  bool   = False

    # Derived at runtime
    lot_size:         float  = 0.0  # USDC per lot = allocated_capital / number_of_lots


# ── Runtime Slot State ────────────────────────────────────────────────────────

class RuntimeSlotState(BaseModel):
    """Full in-memory runtime state for one session slot."""
    session_slot_id:    str
    strategy_slot_id:   str
    slot_state:         SlotState = SlotState.IDLE

    # Market config (None if slot has no assigned market)
    market_config:      Optional[SlotMarketConfig] = None

    # Queued replacement (set by admin action)
    pending_replacement: Optional[SlotMarketConfig] = None

    # Capital tracking
    allocated_capital:  float = 0.0
    deployed_capital:   float = 0.0

    # Aggregate stats
    volume:             float = 0.0
    realized_pnl:       float = 0.0
    unrealized_pnl:     float = 0.0

    class Config:
        use_enum_values = True


# ── Session Runtime Summary ───────────────────────────────────────────────────

class SessionRuntimeSummary(BaseModel):
    session_id:          str
    user_id:             str
    health_state:        SessionHealthState
    status:              str
    gross_capital:       float
    deployable_capital:  float
    total_deployed:      float
    total_volume:        float
    realized_pnl:        float
    unrealized_pnl:      float
    total_pnl:           float
    builder_code_enabled: bool = True
    builder_fees_usdc:   float = 0.0
    slot_count:          int
    active_slot_count:   int
    trade_count:         int = 0
    order_count:         int = 0
    uptime_s:            float
    last_updated:        str = Field(default_factory=_now_iso)

    class Config:
        use_enum_values = True
