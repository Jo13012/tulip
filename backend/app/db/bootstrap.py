"""
bootstrap.py - Small startup schema guards for backwards-compatible columns.
"""
from __future__ import annotations

import logging

from sqlalchemy import text, select

from app.db.session import engine, async_session_factory

log = logging.getLogger("uvicorn.error")


async def ensure_user_profile_columns() -> None:
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name_updated_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name_change_count INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_user_id VARCHAR",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_reward_usdc FLOAT DEFAULT 0.0",
        "CREATE INDEX IF NOT EXISTS ix_users_referrer_user_id ON users(referrer_user_id)",
    ]

    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))

    log.info("Ensured user profile columns are available")


async def ensure_user_free_fee_ends_at_column() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS free_fee_ends_at TIMESTAMPTZ"
        ))
    log.info("Ensured users.free_fee_ends_at column")


async def ensure_strategy_free_fee_column() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE strategies ADD COLUMN IF NOT EXISTS new_user_free_fee_days INTEGER DEFAULT 0"
        ))
    log.info("Ensured strategies.new_user_free_fee_days column")


async def ensure_wallet_funding_columns() -> None:
    statements = [
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS funding_state VARCHAR DEFAULT 'idle'",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS onboarding_error_detail TEXT",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS onboarding_complete BOOLEAN DEFAULT FALSE",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS onboarding_completed_at TIMESTAMPTZ",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS funding_deposit_tx_hash VARCHAR",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS funding_tx_hash VARCHAR",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS funding_deposit_address_cached VARCHAR",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS funding_deposit_cached_at TIMESTAMPTZ",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS funding_last_usdc_observed FLOAT",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS funding_bridge_started_at TIMESTAMPTZ",
        # New deposit address columns (multi-chain)
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS deposit_address_evm VARCHAR",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS deposit_address_svm VARCHAR",
        "ALTER TABLE wallets ADD COLUMN IF NOT EXISTS deposit_addresses_cached_at TIMESTAMPTZ",
        # Backfill: copy legacy EVM address if new column is empty
        """
        UPDATE wallets
        SET deposit_address_evm = funding_deposit_address_cached
        WHERE deposit_address_evm IS NULL
          AND funding_deposit_address_cached IS NOT NULL
          AND funding_deposit_address_cached != ''
        """,
    ]

    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))

    log.info("Ensured wallet funding columns are available")


async def ensure_funding_transactions_table() -> None:
    """Create funding_transactions table if it doesn't exist (idempotent)."""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS funding_transactions (
            id VARCHAR PRIMARY KEY,
            wallet_id VARCHAR NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
            amount_usdc FLOAT NOT NULL,
            state VARCHAR NOT NULL DEFAULT 'initiated',
            tx_hash VARCHAR,
            source VARCHAR NOT NULL DEFAULT 'auto',
            error_detail TEXT,
            initiated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_funding_tx_wallet ON funding_transactions(wallet_id)",
    ]
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))
    log.info("Ensured funding_transactions table")


async def ensure_strategy_slot_version_columns() -> None:
    statements = [
        "ALTER TABLE strategy_slot_versions ADD COLUMN IF NOT EXISTS allocation_pct FLOAT",
        # Backfill existing published rows from the live slot allocation_pct
        """
        UPDATE strategy_slot_versions ssv
        SET allocation_pct = ss.allocation_pct
        FROM strategy_slots ss
        WHERE ssv.strategy_slot_id = ss.id
          AND ssv.allocation_pct IS NULL
          AND ssv.strategy_version_id IS NOT NULL
        """,
    ]
    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))
    log.info("Ensured strategy_slot_versions columns are available")


async def ensure_builder_fees_columns() -> None:
    statements = [
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS builder_fees_usdc FLOAT NOT NULL DEFAULT 0.0",
        "ALTER TABLE session_snapshots ADD COLUMN IF NOT EXISTS builder_fees_usdc FLOAT NOT NULL DEFAULT 0.0",
    ]
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))
    log.info("Ensured builder_fees_usdc columns")


async def ensure_fee_queue_table() -> None:
    """Persistent fee queue — 1 row per wallet, crash-safe."""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS fee_queue (
            privy_wallet_id     VARCHAR PRIMARY KEY,
            wallet_address      VARCHAR NOT NULL,
            referrer_user_id    VARCHAR NOT NULL DEFAULT '',
            referrer_wallet_address VARCHAR NOT NULL DEFAULT '',
            pending_usdc        FLOAT   NOT NULL DEFAULT 0.0,
            last_fill_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            locked              BOOLEAN NOT NULL DEFAULT FALSE
        )
        """,
    ]
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))
        # Unlock any rows left locked by a crashed flush
        await conn.execute(text("UPDATE fee_queue SET locked = FALSE WHERE locked = TRUE"))
    log.info("Ensured fee_queue table")


async def reset_stale_funding_states() -> None:
    """
    On restart, any wallet stuck in bridging/polling has no active job.
    Reset them to idle so the funding flow can re-trigger cleanly.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE wallets SET funding_state = 'idle' "
                "WHERE funding_state IN ('bridging', 'polling', 'submitting') "
                "RETURNING id"
            )
        )
        rows = result.fetchall()
    if rows:
        log.info("Reset %d stale funding_state(s) to idle on startup", len(rows))


async def ensure_global_strategy() -> None:
    """Auto-seed the one global strategy if it doesn't exist yet."""
    from app.db.models import Strategy
    import uuid
    from datetime import datetime, timezone

    async with async_session_factory() as db:
        q = await db.execute(select(Strategy).limit(1))
        if q.scalars().first():
            log.info("Global strategy already exists — skipping seed")
            return

        strat = Strategy(
            id=str(uuid.uuid4()),
            name="Global Strategy",
            status="draft",
            reserve_pct=5.0,
            fee_side="none",
            global_pause=False,
            current_major=1,
            current_minor=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(strat)
        await db.commit()
        log.info("Global strategy seeded: %s", strat.id)
