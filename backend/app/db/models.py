"""
models.py — Full SQLAlchemy ORM schema for multi_user_bot.

Tables:
  users, wallets,
  strategies, strategy_versions, strategy_slots, strategy_slot_versions,
  sessions, session_slots, session_slot_market_history,
  session_orders, session_lots, session_snapshots,
  audit_logs
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float, ForeignKey,
    Integer, Numeric, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship

from app.db.base import Base


def _now():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────
# Users & Wallets
# ─────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id          = Column(String, primary_key=True, default=_uuid)
    privy_user_id = Column(String, unique=True, nullable=True, index=True)
    display_name = Column(String, nullable=True)
    display_name_updated_at = Column(DateTime(timezone=True), nullable=True)
    display_name_change_count = Column(Integer, nullable=False, default=0)
    referrer_user_id = Column(String, nullable=True, index=True)
    referral_reward_usdc = Column(Float, nullable=False, default=0.0)
    is_admin    = Column(Boolean, nullable=False, default=False)
    is_active   = Column(Boolean, nullable=False, default=True)
    # Volume / PnL / Fees (lifetime, event-driven increments)
    trading_volume    = Column(Float,   nullable=False, default=0.0)  # cumulative bot notional (USDC)
    bot_pnl           = Column(Float,   nullable=False, default=0.0)  # cumulative realized PnL across sessions
    fee_override_bps  = Column(Integer, nullable=True,  default=None) # admin manual override; NULL = use XP fee
    effective_fee_bps = Column(Integer, nullable=False, default=120)   # XP-computed fee; updated at volume thresholds only
    free_fee_ends_at  = Column(DateTime(timezone=True), nullable=True, default=None)  # set at onboarding; monitor clears override when passed
    created_at  = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at  = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    wallets   = relationship("Wallet", back_populates="user", cascade="all, delete-orphan")
    sessions  = relationship("Session", back_populates="user")


class Wallet(Base):
    __tablename__ = "wallets"

    id              = Column(String, primary_key=True, default=_uuid)
    user_id         = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    wallet_address   = Column(String, nullable=False)
    proxy_address    = Column(String, nullable=True)
    privy_wallet_id  = Column(String, nullable=True, index=True)
    is_primary      = Column(Boolean, nullable=False, default=True)

    # Polymarket CLOB API credentials (derived once, stored for reuse)
    clob_api_key    = Column(String, nullable=True)
    clob_api_secret = Column(String, nullable=True)
    clob_api_passphrase = Column(String, nullable=True)
    # Onboarding state: pending | deploying | approving | finalizing | ready | failed
    onboarding_state = Column(String, nullable=False, default="pending")
    onboarding_error_detail = Column(Text, nullable=True)
    onboarding_complete = Column(Boolean, nullable=False, default=False)
    onboarding_completed_at = Column(DateTime(timezone=True), nullable=True)
    v2_migration_done = Column(Boolean, nullable=False, default=False)

    # Funding automation state (legacy — kept for compat/history, no longer driven by app)
    funding_state = Column(String, nullable=False, default="idle")
    funding_deposit_tx_hash = Column(String, nullable=True)
    funding_tx_hash = Column(String, nullable=True)
    funding_deposit_address_cached = Column(String, nullable=True)  # legacy EVM cache
    funding_deposit_cached_at = Column(DateTime(timezone=True), nullable=True)
    funding_last_usdc_observed = Column(Float, nullable=True)
    funding_bridge_started_at = Column(DateTime(timezone=True), nullable=True)

    # New deposit addresses (multi-chain, direct user deposit to Polymarket)
    deposit_address_evm = Column(String, nullable=True)   # Arbitrum + Base
    deposit_address_svm = Column(String, nullable=True)   # Solana
    deposit_addresses_cached_at = Column(DateTime(timezone=True), nullable=True)

    created_at      = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at      = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    user     = relationship("User", back_populates="wallets")
    sessions = relationship("Session", back_populates="wallet")
    funding_transactions = relationship(
        "FundingTransaction", back_populates="wallet",
        cascade="all, delete-orphan",
    )
    __table_args__ = (
        UniqueConstraint("user_id", "wallet_address", name="uq_wallet_user_addr"),
    )


# ─────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────

class Strategy(Base):
    __tablename__ = "strategies"

    id           = Column(String, primary_key=True, default=_uuid)
    name         = Column(String, nullable=False)
    status       = Column(String, nullable=False, default="draft")  # draft | published | archived
    reserve_pct  = Column(Float, nullable=False, default=5.0)        # % of gross capital held as fee reserve
    fee_side     = Column(String, nullable=False, default="none")     # none | buy | sell | both
    new_user_free_fee_days = Column(Integer, nullable=False, default=0)  # 0 = disabled
    global_pause = Column(Boolean, nullable=False, default=False)
    current_major = Column(Integer, nullable=False, default=1)
    current_minor = Column(Integer, nullable=False, default=0)
    archived_at  = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at   = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    versions  = relationship("StrategyVersion", back_populates="strategy", order_by="StrategyVersion.published_at")
    slots     = relationship("StrategySlot", back_populates="strategy", order_by="StrategySlot.order_index")
    sessions  = relationship("Session", back_populates="strategy")


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    id             = Column(String, primary_key=True, default=_uuid)
    strategy_id    = Column(String, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True)
    major          = Column(Integer, nullable=False)
    minor          = Column(Integer, nullable=False)
    version_label  = Column(String, nullable=False)   # "1.2"
    published_at   = Column(DateTime(timezone=True), nullable=True)
    created_at     = Column(DateTime(timezone=True), default=_now, nullable=False)

    strategy       = relationship("Strategy", back_populates="versions")
    slot_versions  = relationship("StrategySlotVersion", back_populates="strategy_version")
    sessions       = relationship("Session", back_populates="start_strategy_version",
                                  foreign_keys="Session.start_strategy_version_id")

    __table_args__ = (
        UniqueConstraint("strategy_id", "major", "minor", name="uq_strategy_version"),
    )


class StrategySlot(Base):
    __tablename__ = "strategy_slots"

    id             = Column(String, primary_key=True, default=_uuid)
    strategy_id    = Column(String, ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True)
    label          = Column(String, nullable=False)
    allocation_pct = Column(Float, nullable=False, default=100.0)
    enabled        = Column(Boolean, nullable=False, default=True)
    order_index    = Column(Integer, nullable=False, default=0)
    archived_at    = Column(DateTime(timezone=True), nullable=True)
    created_at     = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at     = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    strategy       = relationship("Strategy", back_populates="slots")
    slot_versions  = relationship("StrategySlotVersion", back_populates="slot")
    session_slots  = relationship("SessionSlot", back_populates="strategy_slot")


class StrategySlotVersion(Base):
    """
    Immutable snapshot of a slot config at publish time.
    Also used to carry active market assignment for each slot.
    """
    __tablename__ = "strategy_slot_versions"

    id                    = Column(String, primary_key=True, default=_uuid)
    strategy_version_id   = Column(String, ForeignKey("strategy_versions.id", ondelete="CASCADE"),
                                   nullable=True, index=True)  # NULL = draft (not yet published)
    strategy_slot_id      = Column(String, ForeignKey("strategy_slots.id", ondelete="CASCADE"),
                                   nullable=False, index=True)
    polymarket_url        = Column(String, nullable=True)
    question              = Column(String, nullable=True)
    condition_id          = Column(String, nullable=True)
    token_id_yes          = Column(String, nullable=True)
    token_id_no           = Column(String, nullable=True)
    side                  = Column(String, nullable=True, default="YES")
    price_min             = Column(Float, nullable=True)
    price_max             = Column(Float, nullable=True)
    max_spread_ticks      = Column(Integer, nullable=True, default=1)
    sl_ticks              = Column(Integer, nullable=True, default=0)
    spread_from_ask       = Column(Boolean, nullable=True, default=False)
    allocation_pct        = Column(Float, nullable=True)   # snapshot of StrategySlot.allocation_pct at publish time
    created_at            = Column(DateTime(timezone=True), default=_now, nullable=False)

    strategy_version = relationship("StrategyVersion", back_populates="slot_versions")
    slot             = relationship("StrategySlot", back_populates="slot_versions")


# ─────────────────────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────────────────────

class Session(Base):
    __tablename__ = "sessions"

    id                          = Column(String, primary_key=True, default=_uuid)
    user_id                     = Column(String, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    wallet_id                   = Column(String, ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False)
    strategy_id                 = Column(String, ForeignKey("strategies.id", ondelete="RESTRICT"), nullable=False)
    start_strategy_version_id   = Column(String, ForeignKey("strategy_versions.id"), nullable=True)

    session_gross_capital       = Column(Float, nullable=False)
    session_deployable_capital  = Column(Float, nullable=False)
    session_fee_reserve         = Column(Float, nullable=False)
    builder_fees_usdc           = Column(Float, nullable=False, default=0.0)

    stop_mode                   = Column(String, nullable=False)   # days | volume
    stop_days                   = Column(Integer, nullable=True)
    target_volume               = Column(Float, nullable=True)
    session_sl_pct              = Column(Float, nullable=False, default=20.0)

    status                      = Column(String, nullable=False, default="pending")
    # pending | starting | running | stopping | stopped | error
    health_state                = Column(String, nullable=False, default="starting")
    # starting | running | idle | recovering | degraded | stopping | stopped | error
    stop_reason                 = Column(Text, nullable=True)

    started_at                  = Column(DateTime(timezone=True), nullable=True)
    stopped_at                  = Column(DateTime(timezone=True), nullable=True)
    last_snapshot_at            = Column(DateTime(timezone=True), nullable=True)
    recovery_deadline_at        = Column(DateTime(timezone=True), nullable=True)
    created_at                  = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at                  = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    user                    = relationship("User", back_populates="sessions")
    wallet                  = relationship("Wallet", back_populates="sessions")
    strategy                = relationship("Strategy", back_populates="sessions")
    start_strategy_version  = relationship("StrategyVersion", back_populates="sessions",
                                           foreign_keys=[start_strategy_version_id])
    session_slots           = relationship("SessionSlot", back_populates="session",
                                           cascade="all, delete-orphan")
    snapshots               = relationship("SessionSnapshot", back_populates="session",
                                           cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_session_user_active", "user_id", "status"),
    )


class SessionSlot(Base):
    __tablename__ = "session_slots"

    id                               = Column(String, primary_key=True, default=_uuid)
    session_id                       = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"),
                                              nullable=False, index=True)
    strategy_slot_id                 = Column(String, ForeignKey("strategy_slots.id"), nullable=False)
    current_strategy_slot_version_id = Column(String, ForeignKey("strategy_slot_versions.id"), nullable=True)
    replacement_strategy_slot_version_id = Column(String, ForeignKey("strategy_slot_versions.id"), nullable=True)

    slot_state         = Column(String, nullable=False, default="idle")
    # active | replacement_queued | waiting_previous_clean | switching | idle | disabled | error
    allocation_pct     = Column(Float, nullable=False)
    allocated_capital  = Column(Float, nullable=False, default=0.0)
    deployed_capital   = Column(Float, nullable=False, default=0.0)
    volume             = Column(Float, nullable=False, default=0.0)
    realized_pnl       = Column(Float, nullable=False, default=0.0)
    unrealized_pnl     = Column(Float, nullable=False, default=0.0)
    paid_fees          = Column(Float, nullable=False, default=0.0)
    created_at         = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at         = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    session        = relationship("Session", back_populates="session_slots")
    strategy_slot  = relationship("StrategySlot", back_populates="session_slots")
    market_history = relationship("SessionSlotMarketHistory", back_populates="session_slot",
                                  cascade="all, delete-orphan")
    lots           = relationship("SessionLot", back_populates="session_slot",
                                  cascade="all, delete-orphan")
    orders         = relationship("SessionOrder", back_populates="session_slot",
                                  cascade="all, delete-orphan")


class SessionSlotMarketHistory(Base):
    """One row per market that was active in a slot during a session."""
    __tablename__ = "session_slot_market_history"

    id                             = Column(String, primary_key=True, default=_uuid)
    session_slot_id                = Column(String, ForeignKey("session_slots.id", ondelete="CASCADE"),
                                           nullable=False, index=True)
    applied_strategy_version_id    = Column(String, ForeignKey("strategy_versions.id"), nullable=True)
    applied_strategy_slot_version_id = Column(String, ForeignKey("strategy_slot_versions.id"), nullable=True)

    polymarket_url  = Column(String, nullable=False)
    question        = Column(String, nullable=True)
    condition_id    = Column(String, nullable=True)
    token_id_yes    = Column(String, nullable=True)
    token_id_no     = Column(String, nullable=True)
    active_token_id = Column(String, nullable=True)
    side            = Column(String, nullable=True)

    started_at   = Column(DateTime(timezone=True), default=_now, nullable=False)
    ended_at     = Column(DateTime(timezone=True), nullable=True)
    close_reason = Column(String, nullable=True)

    volume        = Column(Float, nullable=False, default=0.0)
    realized_pnl  = Column(Float, nullable=False, default=0.0)
    fees_paid     = Column(Float, nullable=False, default=0.0)
    created_at    = Column(DateTime(timezone=True), default=_now, nullable=False)

    session_slot = relationship("SessionSlot", back_populates="market_history")


class SessionOrder(Base):
    """Lean order lifecycle tracking — only important state changes."""
    __tablename__ = "session_orders"

    id               = Column(String, primary_key=True, default=_uuid)
    session_slot_id  = Column(String, ForeignKey("session_slots.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    clob_order_id    = Column(String, nullable=False, index=True)
    side             = Column(String, nullable=False)   # BUY | SELL
    order_type       = Column(String, nullable=False)   # maker | taker | tp | sl
    price            = Column(Float, nullable=False)
    size_usdc        = Column(Float, nullable=False)
    size_tokens      = Column(Float, nullable=True)
    status           = Column(String, nullable=False, default="open")
    # open | partial | filled | cancelled | error
    fill_usdc        = Column(Float, nullable=False, default=0.0)
    fill_tokens      = Column(Float, nullable=False, default=0.0)
    created_at       = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at       = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    session_slot = relationship("SessionSlot", back_populates="orders")


class SessionLot(Base):
    """
    Persistent lot snapshot. Runtime lots live in memory; this table stores
    opened and closed lots for recovery and analytics.
    Compatible with mono_user_bot Lot semantics.
    """
    __tablename__ = "session_lots"

    id               = Column(String, primary_key=True, default=_uuid)
    session_slot_id  = Column(String, ForeignKey("session_slots.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    lot_id           = Column(String, nullable=False, index=True)  # runtime lot_id
    entry_price      = Column(Float, nullable=False)
    target_sell_price = Column(Float, nullable=False)
    size_usdc        = Column(Float, nullable=False)
    tokens           = Column(Float, nullable=False, default=0.0)
    entry_order_id   = Column(String, nullable=True)
    exit_order_id    = Column(String, nullable=True)
    status           = Column(String, nullable=False, default="open")  # open | selling | closed
    pnl              = Column(Float, nullable=False, default=0.0)
    sell_price       = Column(Float, nullable=True)
    timestamp_entry  = Column(DateTime(timezone=True), nullable=True)
    timestamp_exit   = Column(DateTime(timezone=True), nullable=True)
    created_at       = Column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at       = Column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    session_slot = relationship("SessionSlot", back_populates="lots")


class SessionSnapshot(Base):
    """Compact periodic snapshots of session aggregate state. Not hot-path."""
    __tablename__ = "session_snapshots"

    id                = Column(String, primary_key=True, default=_uuid)
    session_id        = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    snapshot_at       = Column(DateTime(timezone=True), default=_now, nullable=False)
    gross_capital     = Column(Float, nullable=False)
    deployable_capital = Column(Float, nullable=False)
    fee_reserve_remaining = Column(Float, nullable=False)
    deployed_capital  = Column(Float, nullable=False, default=0.0)
    total_volume      = Column(Float, nullable=False, default=0.0)
    realized_pnl      = Column(Float, nullable=False, default=0.0)
    unrealized_pnl    = Column(Float, nullable=False, default=0.0)
    paid_fees         = Column(Float, nullable=False, default=0.0)
    builder_fees_usdc = Column(Float, nullable=False, default=0.0)
    health_state      = Column(String, nullable=False)
    created_at        = Column(DateTime(timezone=True), default=_now, nullable=False)

    session = relationship("Session", back_populates="snapshots")


class FundingTransaction(Base):
    """Bridge transaction history (Arbitrum → Polymarket). One row per bridge attempt."""
    __tablename__ = "funding_transactions"

    id           = Column(String, primary_key=True, default=_uuid)
    wallet_id    = Column(String, ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False, index=True)
    amount_usdc  = Column(Float, nullable=False)
    state        = Column(String, nullable=False, default="initiated")
    # initiated | submitted | confirmed | failed
    tx_hash      = Column(String, nullable=True)
    source       = Column(String, nullable=False, default="auto")
    # auto | manual | onboarding
    error_detail = Column(Text, nullable=True)
    initiated_at = Column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    wallet = relationship("Wallet", back_populates="funding_transactions")

    __table_args__ = (
        Index("ix_funding_tx_wallet", "wallet_id"),
    )


class AuditLog(Base):
    """Admin actions, lifecycle events, recovery events, slot replacements, etc."""
    __tablename__ = "audit_logs"

    id          = Column(String, primary_key=True, default=_uuid)
    actor_id    = Column(String, nullable=True)   # user_id or "system"
    actor_role  = Column(String, nullable=True)   # admin | user | system
    action      = Column(String, nullable=False)
    # e.g. strategy.publish, slot.replace_queued, session.force_stop,
    #      session.recovery_start, session.recovery_success, global.pause
    entity_type = Column(String, nullable=True)
    entity_id   = Column(String, nullable=True)
    detail      = Column(Text, nullable=True)    # JSON string with extra context
    created_at  = Column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_actor",  "actor_id"),
    )
