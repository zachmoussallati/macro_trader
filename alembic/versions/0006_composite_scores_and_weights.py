"""signals.composite_scores + signals.composite_weights

Stage 7 composite scoring layer.

- ``signals.composite_scores`` — hypertable on ``value_ts``; one
  row per (method, instrument, value_ts, observation_ts) carrying
  the regime-conditional composite score + decomposition metadata.
- ``signals.composite_weights`` — snapshot table; one row per
  (method, snapshot_ts, regime_label, signal_method_id). Stage 7
  computes a new snapshot weekly after attribution refresh; daily
  scoring queries the latest snapshot at or before ``as_of``.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # signals schema already exists from migration 0003.
    op.create_table(
        "composite_scores",
        sa.Column(
            "method_id",
            sa.String(128),
            sa.ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "instrument_id",
            sa.String(32),
            sa.ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("value_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column(
            "observation_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column("raw_score", sa.Numeric(20, 10), nullable=True),
        sa.Column("score", sa.Numeric(8, 6), nullable=True),
        sa.Column("confidence", sa.Numeric(8, 6), nullable=True),
        sa.Column("regime_label", sa.String(64), nullable=True),
        sa.Column("n_signals_used", sa.Integer, nullable=True),
        sa.Column(
            "composite_metadata",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="signals",
    )
    op.create_index(
        "ix_composite_scores_method_id",
        "composite_scores",
        ["method_id"],
        schema="signals",
    )
    op.create_index(
        "ix_composite_scores_instrument_id",
        "composite_scores",
        ["instrument_id"],
        schema="signals",
    )
    op.create_index(
        "ix_composite_scores_observation_ts",
        "composite_scores",
        ["observation_ts"],
        schema="signals",
    )

    op.create_table(
        "composite_weights",
        sa.Column(
            "method_id",
            sa.String(128),
            sa.ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "snapshot_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True
        ),
        sa.Column("regime_label", sa.String(64), primary_key=True),
        sa.Column("signal_method_id", sa.String(128), primary_key=True),
        sa.Column("weight", sa.Numeric(12, 8), nullable=False),
        sa.Column("weight_source", sa.String(32), nullable=False),
        sa.Column(
            "weight_metadata",
            postgresql.JSONB,
            nullable=False,
            server_default="{}",
        ),
        schema="signals",
    )
    op.create_index(
        "ix_composite_weights_method_snapshot",
        "composite_weights",
        ["method_id", "snapshot_ts"],
        schema="signals",
    )

    bind = op.get_bind()
    has_timescale = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    if has_timescale:
        op.execute(
            sa.text(
                "SELECT create_hypertable('signals.composite_scores', 'value_ts', "
                "if_not_exists => TRUE, migrate_data => TRUE)"
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_composite_weights_method_snapshot",
        table_name="composite_weights",
        schema="signals",
    )
    op.drop_table("composite_weights", schema="signals")
    op.drop_index(
        "ix_composite_scores_observation_ts",
        table_name="composite_scores",
        schema="signals",
    )
    op.drop_index(
        "ix_composite_scores_instrument_id",
        table_name="composite_scores",
        schema="signals",
    )
    op.drop_index(
        "ix_composite_scores_method_id",
        table_name="composite_scores",
        schema="signals",
    )
    op.drop_table("composite_scores", schema="signals")
