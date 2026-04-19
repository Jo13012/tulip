"""
session.py — Async SQLAlchemy engine + session factory.
DB timing is logged via SQLAlchemy echo=True is too noisy;
use X-Response-Time-Ms header + perf logger in middleware instead.

Supabase PgBouncer (transaction mode) compatibility:
  - prepared_statement_cache_size=0     → disable SQLAlchemy's LRU cache
  - prepared_statement_name_func=""     → unnamed prepared statements
                                          (PgBouncer allows these in transaction mode)
  - statement_cache_size=0              → disable asyncpg's own cache
  - pool_pre_ping=True                  → discard stale connections
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from app.core.config import settings

_base = settings.DATABASE_URL
_sep = "&" if "?" in _base else "?"
_url = f"{_base}{_sep}prepared_statement_cache_size=0"

engine = create_async_engine(
    _url,
    poolclass=NullPool,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: "",
        "server_settings": {"jit": "off"},
    },
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
