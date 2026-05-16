"""market_data.options_chains + market_data.options_surfaces

Stage 5 vol surface family. Both tables are TimescaleDB hypertables
on ``snapshot_ts``; options_chains is wide / row-per-strike,
options_surfaces is narrow / row-per-(instrument, method).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "options_chains",
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("snapshot_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column("expiry_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column("strike", sa.Numeric(20, 6), primary_key=True),
        sa.Column("option_type", sa.String(8), primary_key=True),
        sa.Column("bid", sa.Numeric(20, 6), nullable=True),
        sa.Column("ask", sa.Numeric(20, 6), nullable=True),
        sa.Column("last", sa.Numeric(20, 6), nullable=True),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.Column("open_interest", sa.Integer(), nullable=True),
        sa.Column("implied_vol", sa.Numeric(12, 6), nullable=True),
        sa.Column("delta", sa.Numeric(12, 6), nullable=True),
        sa.Column("gamma", sa.Numeric(12, 6), nullable=True),
        sa.Column("vega", sa.Numeric(12, 6), nullable=True),
        sa.Column("theta", sa.Numeric(12, 6), nullable=True),
        sa.Column("underlying_price", sa.Numeric(20, 6), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="market_data",
    )
    op.create_index(
        "ix_options_chains_instrument_expiry",
        "options_chains",
        ["instrument_id", "expiry_ts"],
        schema="market_data",
    )

    op.create_table(
        "options_surfaces",
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("snapshot_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column("method", sa.String(32), primary_key=True),
        sa.Column("parameters", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("surface_metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="market_data",
    )

    bind = op.get_bind()
    has_timescale = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    if has_timescale:
        op.execute(
            sa.text(
                "SELECT create_hypertable('market_data.options_chains', "
                "'snapshot_ts', if_not_exists => TRUE, migrate_data => TRUE)"
            )
        )
        op.execute(
            sa.text(
                "SELECT create_hypertable('market_data.options_surfaces', "
                "'snapshot_ts', if_not_exists => TRUE, migrate_data => TRUE)"
            )
        )


def downgrade() -> None:
    op.drop_table("options_surfaces", schema="market_data")
    op.drop_index(
        "ix_options_chains_instrument_expiry",
        table_name="options_chains",
        schema="market_data",
    )
    op.drop_table("options_chains", schema="market_data")
