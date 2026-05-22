"""portfolio schema: covariance + positions + equity_curve + drawdown_state

Stage 8 portfolio construction layer.

- ``portfolio.covariance_estimates`` (hypertable on as_of)
- ``portfolio.volatility_estimates`` (hypertable on as_of)
- ``portfolio.positions`` (hypertable on as_of)
- ``portfolio.equity_curve`` (hypertable on as_of)
- ``portfolio.drawdown_state`` (state row per method, not hypertable)

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS portfolio"))

    op.create_table(
        "covariance_estimates",
        sa.Column(
            "method_id",
            sa.String(128),
            sa.ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("as_of", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column(
            "instrument_a",
            sa.String(32),
            sa.ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "instrument_b",
            sa.String(32),
            sa.ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("covariance", sa.Numeric(28, 14), nullable=True),
        sa.Column("correlation", sa.Numeric(12, 8), nullable=True),
        sa.Column("lookback_days", sa.Integer, nullable=True),
        sa.Column(
            "cov_metadata", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        schema="portfolio",
    )
    op.create_index(
        "ix_covariance_method_asof",
        "covariance_estimates",
        ["method_id", "as_of"],
        schema="portfolio",
    )

    op.create_table(
        "volatility_estimates",
        sa.Column(
            "method_id",
            sa.String(128),
            sa.ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("as_of", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("volatility", sa.Numeric(20, 10), nullable=True),
        sa.Column("lookback_days", sa.Integer, nullable=True),
        sa.Column(
            "vol_metadata", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        schema="portfolio",
    )

    op.create_table(
        "positions",
        sa.Column(
            "method_id",
            sa.String(128),
            sa.ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("as_of", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("target_weight", sa.Numeric(12, 8), nullable=False),
        sa.Column("pre_gate_weight", sa.Numeric(12, 8), nullable=True),
        sa.Column("expected_vol_contribution", sa.Numeric(12, 8), nullable=True),
        sa.Column("composite_score", sa.Numeric(8, 6), nullable=True),
        sa.Column("block", sa.String(32), nullable=True),
        sa.Column("gate_level", sa.String(16), nullable=True),
        sa.Column("gate_scaling_factor", sa.Numeric(8, 6), nullable=True),
        sa.Column(
            "position_metadata",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="portfolio",
    )
    op.create_index(
        "ix_positions_method_asof",
        "positions",
        ["method_id", "as_of"],
        schema="portfolio",
    )

    op.create_table(
        "equity_curve",
        sa.Column(
            "method_id",
            sa.String(128),
            sa.ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("as_of", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column("nav", sa.Numeric(28, 14), nullable=False),
        sa.Column("daily_return", sa.Numeric(20, 10), nullable=True),
        sa.Column("cumulative_return", sa.Numeric(20, 10), nullable=True),
        sa.Column("peak_nav", sa.Numeric(28, 14), nullable=True),
        sa.Column("drawdown_from_peak", sa.Numeric(12, 8), nullable=True),
        sa.Column(
            "equity_metadata", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        schema="portfolio",
    )

    op.create_table(
        "drawdown_state",
        sa.Column(
            "method_id",
            sa.String(128),
            sa.ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("current_gate_level", sa.String(16), nullable=False),
        sa.Column(
            "level_1_triggered_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "level_1_release_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "level_2_triggered_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "level_2_release_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "level_3_triggered_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column("effective_scaling_factor", sa.Numeric(8, 6), nullable=False),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "drawdown_metadata",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        schema="portfolio",
    )

    bind = op.get_bind()
    has_timescale = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    if has_timescale:
        for table in (
            "covariance_estimates",
            "volatility_estimates",
            "positions",
            "equity_curve",
        ):
            op.execute(
                sa.text(
                    f"SELECT create_hypertable('portfolio.{table}', 'as_of', "
                    "if_not_exists => TRUE, migrate_data => TRUE)"
                )
            )


def downgrade() -> None:
    op.drop_table("drawdown_state", schema="portfolio")
    op.drop_table("equity_curve", schema="portfolio")
    op.drop_index(
        "ix_positions_method_asof",
        table_name="positions",
        schema="portfolio",
    )
    op.drop_table("positions", schema="portfolio")
    op.drop_table("volatility_estimates", schema="portfolio")
    op.drop_index(
        "ix_covariance_method_asof",
        table_name="covariance_estimates",
        schema="portfolio",
    )
    op.drop_table("covariance_estimates", schema="portfolio")
    op.execute(sa.text("DROP SCHEMA IF EXISTS portfolio"))
