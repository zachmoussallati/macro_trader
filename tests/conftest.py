"""Pytest configuration and shared fixtures.

Two layers of fixtures:

- ``test_settings``    — forces ``APP_ENV=test`` and reloads the config cache.
- ``pg_engine``        — a SQLAlchemy engine bound to the test database.
                         Skipped automatically if Postgres is not reachable.
- ``db_session``       — a transactional session per test (savepoint rolled
                         back on teardown). Used for integration tests.

Unit tests should not depend on ``pg_engine`` / ``db_session`` — they work
with the in-process registry only.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

# Force test env before any imports that read config. We OVERWRITE rather
# than setdefault so a caller's shell exports (e.g. POSTGRES_DB=macro_trader
# in a dev session) don't bleed into the test DB.
os.environ["APP_ENV"] = "test"
os.environ["POSTGRES_DB"] = "macro_trader_test"
os.environ.setdefault("POSTGRES_PASSWORD", "change_me_dev_password")
os.environ.setdefault("POSTGRES_USER", "macro")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ["DATABASE_URL"] = ""  # let config rebuild from parts


@pytest.fixture(scope="session", autouse=True)
def _reset_config_cache() -> None:
    """Ensure config cache is fresh now that env vars are set."""
    from macro_trader.config import reset_settings_cache

    reset_settings_cache()


@pytest.fixture(scope="session")
def test_settings() -> Any:
    from macro_trader.config import get_settings

    return get_settings()


def _postgres_reachable(settings: Any) -> bool:
    url = settings.database.url.replace(settings.database.name, "postgres")
    engine = create_engine(url, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    except Exception:
        return False
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def pg_engine(test_settings: Any) -> Generator[Engine, None, None]:
    """Per-session engine on the test DB; skips if Postgres unreachable."""
    if not _postgres_reachable(test_settings):
        pytest.skip("Postgres not reachable; skipping integration tests")

    admin_url = test_settings.database.url.replace(test_settings.database.name, "postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": test_settings.database.name},
            ).first()
            if exists is None:
                conn.execute(text(f'CREATE DATABASE "{test_settings.database.name}"'))
    finally:
        admin_engine.dispose()

    engine = create_engine(test_settings.database.url, future=True)

    # Bring schema up to date.
    from alembic import command
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", test_settings.database.url)
    command.upgrade(cfg, "head")

    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(pg_engine: Engine) -> Generator[Session, None, None]:
    """Transactional session per test."""
    connection = pg_engine.connect()
    trans = connection.begin()
    SessionLocal = sessionmaker(bind=connection, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _fresh_in_memory_registry() -> Generator[None, None, None]:
    """Reset the module-level methods registry between tests so they don't leak."""
    from macro_trader.methods.registry import get_default_registry

    get_default_registry().clear()
    yield
    get_default_registry().clear()
