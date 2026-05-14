"""Integration tests for the DB layer."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from macro_trader.db.schemas import DOMAIN_SCHEMAS


@pytest.mark.integration
def test_all_domain_schemas_exist(db_session) -> None:
    res = db_session.execute(
        text("SELECT nspname FROM pg_namespace WHERE nspname = ANY(:names)"),
        {"names": list(DOMAIN_SCHEMAS)},
    ).all()
    present = {r[0] for r in res}
    assert set(DOMAIN_SCHEMAS).issubset(present), (
        f"missing schemas: {set(DOMAIN_SCHEMAS) - present}"
    )


@pytest.mark.integration
def test_timescaledb_enabled(db_session) -> None:
    ver = db_session.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar()
    assert ver is not None, "TimescaleDB extension not enabled"


@pytest.mark.integration
def test_system_methods_registry_table_exists(pg_engine) -> None:
    insp = inspect(pg_engine)
    tables = insp.get_table_names(schema="system")
    assert "methods_registry" in tables
    assert "method_status_history" in tables
    assert "method_comparisons" in tables
    assert "heartbeat" in tables


@pytest.mark.integration
def test_auth_users_table_exists(pg_engine) -> None:
    insp = inspect(pg_engine)
    tables = insp.get_table_names(schema="auth")
    assert "users" in tables
    assert "refresh_tokens" in tables
