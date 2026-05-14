"""stage 2 data layer schemas

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _tstz(*, nullable: bool = False, default: bool = False) -> sa.Column:
    kwargs: dict = {"nullable": nullable}
    if default:
        kwargs["server_default"] = sa.text("now()")
    return postgresql.TIMESTAMP(timezone=True), kwargs  # type: ignore[return-value]


HYPERTABLES: tuple[tuple[str, str, str], ...] = (
    ("market_data", "daily_bars", "value_ts"),
    ("macro_data", "series_observations", "observation_ts"),
    ("positioning", "cot_weekly", "report_ts"),
    ("alt_data", "eia_inventory", "value_ts"),
    ("alt_data", "usda_reports", "value_ts"),
    ("alt_data", "weather_data", "value_ts"),
    ("alt_data", "google_trends", "value_ts"),
)


# ----------------------------------------------------------------------
# Upgrade
# ----------------------------------------------------------------------
def upgrade() -> None:
    # =====================================================
    # market_data.instruments
    # =====================================================
    op.create_table(
        "instruments",
        sa.Column("instrument_id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("asset_class", sa.String(32), nullable=False),
        sa.Column("sub_class", sa.String(64), nullable=True),
        sa.Column("proxy_ticker", sa.String(32), nullable=True),
        sa.Column("proxy_type", sa.String(16), nullable=True),
        sa.Column("underlying_ref", sa.String(64), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("exchange", sa.String(16), nullable=True),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column("tracking_error_notes", sa.Text, nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        schema="market_data",
    )
    op.create_index(
        "ix_instruments_asset_class",
        "instruments",
        ["asset_class"],
        schema="market_data",
    )
    op.create_index(
        "ix_instruments_is_active",
        "instruments",
        ["is_active"],
        schema="market_data",
    )

    # =====================================================
    # market_data.daily_bars
    # =====================================================
    op.create_table(
        "daily_bars",
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey(
                "market_data.instruments.instrument_id", ondelete="CASCADE"
            ),
            primary_key=True,
        ),
        sa.Column(
            "value_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column(
            "observation_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column("open", sa.Numeric(20, 8), nullable=True),
        sa.Column("high", sa.Numeric(20, 8), nullable=True),
        sa.Column("low", sa.Numeric(20, 8), nullable=True),
        sa.Column("close", sa.Numeric(20, 8), nullable=True),
        sa.Column("volume", sa.Numeric(24, 4), nullable=True),
        sa.Column("adjusted_close", sa.Numeric(20, 8), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=True),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "is_revised", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        schema="market_data",
    )
    op.create_index(
        "ix_daily_bars_instrument",
        "daily_bars",
        ["instrument_id"],
        schema="market_data",
    )
    op.create_index(
        "ix_daily_bars_observation_ts",
        "daily_bars",
        ["observation_ts"],
        schema="market_data",
    )

    # =====================================================
    # macro_data.series
    # =====================================================
    op.create_table(
        "series",
        sa.Column("series_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("units", sa.String(64), nullable=True),
        sa.Column("seasonal_adjustment", sa.String(8), nullable=True),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column(
            "affected_instruments",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column(
            "metadata", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        schema="macro_data",
    )
    op.create_index("ix_series_source", "series", ["source"], schema="macro_data")
    op.create_index("ix_series_category", "series", ["category"], schema="macro_data")
    op.create_index(
        "ix_series_is_active", "series", ["is_active"], schema="macro_data"
    )

    # =====================================================
    # macro_data.series_observations
    # =====================================================
    op.create_table(
        "series_observations",
        sa.Column(
            "series_id",
            sa.String(128),
            sa.ForeignKey("macro_data.series.series_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "value_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column(
            "observation_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column("value", sa.Numeric(28, 10), nullable=True),
        sa.Column("realtime_start", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("realtime_end", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "is_initial",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "revision_number",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=True),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="macro_data",
    )
    op.create_index(
        "ix_series_obs_series_value",
        "series_observations",
        ["series_id", "value_ts"],
        schema="macro_data",
    )
    op.create_index(
        "ix_series_obs_realtime",
        "series_observations",
        ["realtime_start", "realtime_end"],
        schema="macro_data",
    )

    # =====================================================
    # macro_data.calendar_events
    # =====================================================
    op.create_table(
        "calendar_events",
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("event_ts", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "actual_release_ts", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(256), nullable=False),
        sa.Column("region", sa.String(16), nullable=True),
        sa.Column("importance", sa.String(16), nullable=False),
        sa.Column(
            "affected_instruments",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column(
            "affected_series",
            postgresql.ARRAY(sa.String(128)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        schema="macro_data",
    )
    op.create_index(
        "ix_calendar_events_event_ts",
        "calendar_events",
        ["event_ts"],
        schema="macro_data",
    )
    op.create_index(
        "ix_calendar_events_kind",
        "calendar_events",
        ["kind"],
        schema="macro_data",
    )
    op.create_index(
        "ix_calendar_events_importance",
        "calendar_events",
        ["importance"],
        schema="macro_data",
    )

    # =====================================================
    # positioning.cot_weekly
    # =====================================================
    op.create_table(
        "cot_weekly",
        sa.Column(
            "report_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey(
                "market_data.instruments.instrument_id", ondelete="CASCADE"
            ),
            primary_key=True,
        ),
        sa.Column("report_type", sa.String(32), primary_key=True),
        sa.Column(
            "publication_ts", postgresql.TIMESTAMP(timezone=True), nullable=False
        ),
        sa.Column("cftc_contract_code", sa.String(16), nullable=False),
        sa.Column("open_interest", sa.Numeric(24, 4), nullable=True),
        sa.Column("producer_long", sa.Numeric(24, 4), nullable=True),
        sa.Column("producer_short", sa.Numeric(24, 4), nullable=True),
        sa.Column("swap_long", sa.Numeric(24, 4), nullable=True),
        sa.Column("swap_short", sa.Numeric(24, 4), nullable=True),
        sa.Column("managed_money_long", sa.Numeric(24, 4), nullable=True),
        sa.Column("managed_money_short", sa.Numeric(24, 4), nullable=True),
        sa.Column("other_reportable_long", sa.Numeric(24, 4), nullable=True),
        sa.Column("other_reportable_short", sa.Numeric(24, 4), nullable=True),
        sa.Column("nonreportable_long", sa.Numeric(24, 4), nullable=True),
        sa.Column("nonreportable_short", sa.Numeric(24, 4), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="cftc"),
        sa.Column("source_version", sa.String(64), nullable=True),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="positioning",
    )
    op.create_index(
        "ix_cot_weekly_instrument",
        "cot_weekly",
        ["instrument_id"],
        schema="positioning",
    )
    op.create_index(
        "ix_cot_weekly_publication",
        "cot_weekly",
        ["publication_ts"],
        schema="positioning",
    )

    # =====================================================
    # alt_data.eia_inventory
    # =====================================================
    op.create_table(
        "eia_inventory",
        sa.Column("series_id", sa.String(64), primary_key=True),
        sa.Column(
            "value_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column(
            "observation_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column("value", sa.Numeric(20, 4), nullable=True),
        sa.Column("units", sa.String(32), nullable=True),
        sa.Column(
            "affected_instruments",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("source", sa.String(32), nullable=False, server_default="eia"),
        sa.Column("source_version", sa.String(64), nullable=True),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="alt_data",
    )
    op.create_index(
        "ix_eia_inventory_series",
        "eia_inventory",
        ["series_id"],
        schema="alt_data",
    )

    # =====================================================
    # alt_data.usda_reports
    # =====================================================
    op.create_table(
        "usda_reports",
        sa.Column(
            "report_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "value_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column("report_type", sa.String(32), nullable=False),
        sa.Column(
            "observation_ts", postgresql.TIMESTAMP(timezone=True), nullable=False
        ),
        sa.Column("commodity", sa.String(32), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value", sa.Numeric(20, 4), nullable=True),
        sa.Column("units", sa.String(32), nullable=True),
        sa.Column(
            "affected_instruments",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("source", sa.String(32), nullable=False, server_default="usda"),
        sa.Column("source_version", sa.String(64), nullable=True),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="alt_data",
    )
    op.create_index(
        "ix_usda_reports_report_type",
        "usda_reports",
        ["report_type"],
        schema="alt_data",
    )
    op.create_index(
        "ix_usda_reports_commodity",
        "usda_reports",
        ["commodity"],
        schema="alt_data",
    )

    # =====================================================
    # alt_data.weather_data
    # =====================================================
    op.create_table(
        "weather_data",
        sa.Column("station_id", sa.String(64), primary_key=True),
        sa.Column(
            "value_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column("metric", sa.String(32), primary_key=True),
        sa.Column(
            "observation_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column("value", sa.Numeric(20, 4), nullable=True),
        sa.Column("region", sa.String(64), nullable=True),
        sa.Column(
            "affected_instruments",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("source", sa.String(32), nullable=False, server_default="noaa"),
        sa.Column("source_version", sa.String(64), nullable=True),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="alt_data",
    )
    op.create_index("ix_weather_region", "weather_data", ["region"], schema="alt_data")
    op.create_index("ix_weather_metric", "weather_data", ["metric"], schema="alt_data")

    # =====================================================
    # alt_data.google_trends
    # =====================================================
    op.create_table(
        "google_trends",
        sa.Column("query_term", sa.String(128), primary_key=True),
        sa.Column("region", sa.String(8), primary_key=True),
        sa.Column(
            "value_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column(
            "observation_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column("value", sa.Numeric(8, 2), nullable=True),
        sa.Column(
            "affected_instruments",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column(
            "source", sa.String(32), nullable=False, server_default="google_trends"
        ),
        sa.Column("source_version", sa.String(64), nullable=True),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="alt_data",
    )
    op.create_index(
        "ix_google_trends_query",
        "google_trends",
        ["query_term"],
        schema="alt_data",
    )

    # =====================================================
    # system.data_sources
    # =====================================================
    op.create_table(
        "data_sources",
        sa.Column("source_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("base_url", sa.String(256), nullable=True),
        sa.Column("api_version", sa.String(32), nullable=True),
        sa.Column(
            "requires_auth",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("rate_limit_notes", sa.Text, nullable=True),
        sa.Column(
            "last_health_check", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "is_healthy",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "metadata", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        schema="system",
    )

    # =====================================================
    # system.data_lineage
    # =====================================================
    op.create_table(
        "data_lineage",
        sa.Column(
            "lineage_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("system.data_sources.source_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("fetched_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("fetch_method", sa.String(32), nullable=True),
        sa.Column("source_version", sa.String(64), nullable=True),
        sa.Column("transformation_version", sa.String(32), nullable=True),
        sa.Column("rows_ingested", sa.Integer, nullable=True),
        sa.Column("rows_updated", sa.Integer, nullable=True),
        sa.Column("rows_rejected", sa.Integer, nullable=True),
        sa.Column(
            "error_count", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "metadata", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.Column("dagster_run_id", sa.String(64), nullable=True),
        sa.Column("dagster_asset_key", sa.String(128), nullable=True),
        schema="system",
    )
    op.create_index(
        "ix_data_lineage_source", "data_lineage", ["source_id"], schema="system"
    )
    op.create_index(
        "ix_data_lineage_fetched_at",
        "data_lineage",
        ["fetched_at"],
        schema="system",
    )

    # =====================================================
    # system.data_freshness
    # =====================================================
    op.create_table(
        "data_freshness",
        sa.Column(
            "source_id",
            sa.String(64),
            sa.ForeignKey("system.data_sources.source_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("series_or_table", sa.String(128), primary_key=True),
        sa.Column("expected_frequency", sa.String(16), nullable=False),
        sa.Column("expected_delay_seconds", sa.Integer, nullable=True),
        sa.Column(
            "last_successful_at", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "last_attempted_at", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "is_stale", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "consecutive_failures",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        schema="system",
    )

    # =====================================================
    # system.data_quality_flags
    # =====================================================
    op.create_table(
        "data_quality_flags",
        sa.Column(
            "flag_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "method_id",
            sa.String(128),
            sa.ForeignKey(
                "system.methods_registry.method_id", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column("series_id", sa.String(128), nullable=False),
        sa.Column("value_ts", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("value", sa.Numeric(28, 10), nullable=True),
        sa.Column("is_flagged", sa.Boolean, nullable=False),
        sa.Column("confidence", sa.Numeric(8, 6), nullable=True),
        sa.Column("run_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="system",
    )
    op.create_index(
        "ix_dq_flags_method",
        "data_quality_flags",
        ["method_id"],
        schema="system",
    )
    op.create_index(
        "ix_dq_flags_series_value",
        "data_quality_flags",
        ["series_id", "value_ts"],
        schema="system",
    )
    op.create_index(
        "ix_dq_flags_run_at",
        "data_quality_flags",
        ["run_at"],
        schema="system",
    )

    # =====================================================
    # TimescaleDB hypertables (best-effort: only if extension present)
    # =====================================================
    bind = op.get_bind()
    has_timescale = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    if has_timescale:
        for schema, table, time_col in HYPERTABLES:
            op.execute(
                sa.text(
                    f"SELECT create_hypertable('{schema}.{table}', '{time_col}', "
                    "if_not_exists => TRUE, migrate_data => TRUE)"
                )
            )


# ----------------------------------------------------------------------
# Downgrade
# ----------------------------------------------------------------------
def downgrade() -> None:
    op.drop_index(
        "ix_dq_flags_run_at", table_name="data_quality_flags", schema="system"
    )
    op.drop_index(
        "ix_dq_flags_series_value",
        table_name="data_quality_flags",
        schema="system",
    )
    op.drop_index(
        "ix_dq_flags_method", table_name="data_quality_flags", schema="system"
    )
    op.drop_table("data_quality_flags", schema="system")

    op.drop_table("data_freshness", schema="system")

    op.drop_index(
        "ix_data_lineage_fetched_at", table_name="data_lineage", schema="system"
    )
    op.drop_index(
        "ix_data_lineage_source", table_name="data_lineage", schema="system"
    )
    op.drop_table("data_lineage", schema="system")
    op.drop_table("data_sources", schema="system")

    op.drop_index(
        "ix_google_trends_query", table_name="google_trends", schema="alt_data"
    )
    op.drop_table("google_trends", schema="alt_data")

    op.drop_index("ix_weather_metric", table_name="weather_data", schema="alt_data")
    op.drop_index("ix_weather_region", table_name="weather_data", schema="alt_data")
    op.drop_table("weather_data", schema="alt_data")

    op.drop_index(
        "ix_usda_reports_commodity", table_name="usda_reports", schema="alt_data"
    )
    op.drop_index(
        "ix_usda_reports_report_type",
        table_name="usda_reports",
        schema="alt_data",
    )
    op.drop_table("usda_reports", schema="alt_data")

    op.drop_index(
        "ix_eia_inventory_series", table_name="eia_inventory", schema="alt_data"
    )
    op.drop_table("eia_inventory", schema="alt_data")

    op.drop_index(
        "ix_cot_weekly_publication", table_name="cot_weekly", schema="positioning"
    )
    op.drop_index(
        "ix_cot_weekly_instrument", table_name="cot_weekly", schema="positioning"
    )
    op.drop_table("cot_weekly", schema="positioning")

    op.drop_index(
        "ix_calendar_events_importance",
        table_name="calendar_events",
        schema="macro_data",
    )
    op.drop_index(
        "ix_calendar_events_kind", table_name="calendar_events", schema="macro_data"
    )
    op.drop_index(
        "ix_calendar_events_event_ts",
        table_name="calendar_events",
        schema="macro_data",
    )
    op.drop_table("calendar_events", schema="macro_data")

    op.drop_index(
        "ix_series_obs_realtime",
        table_name="series_observations",
        schema="macro_data",
    )
    op.drop_index(
        "ix_series_obs_series_value",
        table_name="series_observations",
        schema="macro_data",
    )
    op.drop_table("series_observations", schema="macro_data")

    op.drop_index("ix_series_is_active", table_name="series", schema="macro_data")
    op.drop_index("ix_series_category", table_name="series", schema="macro_data")
    op.drop_index("ix_series_source", table_name="series", schema="macro_data")
    op.drop_table("series", schema="macro_data")

    op.drop_index(
        "ix_daily_bars_observation_ts",
        table_name="daily_bars",
        schema="market_data",
    )
    op.drop_index(
        "ix_daily_bars_instrument", table_name="daily_bars", schema="market_data"
    )
    op.drop_table("daily_bars", schema="market_data")

    op.drop_index(
        "ix_instruments_is_active", table_name="instruments", schema="market_data"
    )
    op.drop_index(
        "ix_instruments_asset_class",
        table_name="instruments",
        schema="market_data",
    )
    op.drop_table("instruments", schema="market_data")
