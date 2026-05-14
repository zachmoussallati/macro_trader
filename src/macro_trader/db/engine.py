"""SQLAlchemy engine + session factories.

The engine is lazily constructed once per process. Tests use a separate
fixture that builds its own engine against a test database.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from macro_trader.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Process-wide engine. Lazy-initialised on first call."""
    settings = get_settings()
    return create_engine(
        settings.database.url,
        echo=settings.database.echo_sql,
        pool_pre_ping=True,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        future=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


@contextmanager
def get_session() -> Iterator[Session]:
    """Context-managed session. Commits on success, rolls back on exception."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Test hook: drop the cached engine so a fresh one is built. Call this
    after monkey-patching settings."""
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
