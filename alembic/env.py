"""Alembic env.

The URL is sourced from our config layer (not alembic.ini) so the same
sqlalchemy.url is used everywhere. ``version_table_schema`` is pinned to
``system`` so the alembic bookkeeping lives alongside our other system tables.
"""

from __future__ import annotations

import contextlib
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import macro_trader.db.models  # noqa: F401 — registers models with metadata
from macro_trader.config import get_settings
from macro_trader.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database.url)

target_metadata = Base.metadata


_TIMESCALE_SCHEMAS = frozenset(
    {
        "_timescaledb_cache",
        "_timescaledb_catalog",
        "_timescaledb_config",
        "_timescaledb_internal",
        "_timescaledb_functions",
        "_timescaledb_debug",
        "timescaledb_information",
        "timescaledb_experimental",
    }
)


# `create_hypertable` auto-creates a descending btree index named
# `<table>_<time_col>_idx`. Skip these during autogenerate compare so
# `alembic check` doesn't constantly want to drop them.
_TIMESCALE_AUTO_INDEXES = frozenset(
    {
        "daily_bars_value_ts_idx",
        "series_observations_observation_ts_idx",
        "cot_weekly_report_ts_idx",
        "eia_inventory_value_ts_idx",
        "usda_reports_value_ts_idx",
        "weather_data_value_ts_idx",
        "google_trends_value_ts_idx",
    }
)


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Skip TimescaleDB internal schemas, every object inside them, and the
    auto-created hypertable indexes."""
    if type_ == "schema" and name in _TIMESCALE_SCHEMAS:
        return False
    schema = getattr(obj, "schema", None)
    if schema in _TIMESCALE_SCHEMAS:
        return False
    return not (type_ == "index" and reflected and name in _TIMESCALE_AUTO_INDEXES)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version",
        version_table_schema="system",
        include_schemas=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB."""
    from sqlalchemy import text

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Ensure required extensions exist BEFORE alembic tries to create
        # uuid-defaulted columns. Idempotent.
        connection.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        # TimescaleDB ships with the timescaledb/timescaledb image but must
        # be enabled per database. Best-effort: tests that don't need it
        # will pass regardless.
        with contextlib.suppress(Exception):
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        # Ensure the `system` schema exists BEFORE alembic tries to create
        # its version table inside it.
        connection.execute(text('CREATE SCHEMA IF NOT EXISTS "system"'))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version",
            version_table_schema="system",
            include_schemas=True,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
