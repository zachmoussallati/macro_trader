"""signals.signal_values

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "signal_values",
        sa.Column(
            "signal_id",
            sa.String(128),
            sa.ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
            primary_key=True,
        ),
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
        sa.Column("raw_value", sa.Numeric(28, 10), nullable=True),
        sa.Column("zscore", sa.Numeric(20, 10), nullable=True),
        sa.Column("rank", sa.Numeric(8, 6), nullable=True),
        sa.Column("confidence", sa.Numeric(8, 6), nullable=True),
        sa.Column("rolling_sharpe_252", sa.Numeric(12, 6), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="signals",
    )
    op.create_index(
        "ix_signal_values_signal_id",
        "signal_values",
        ["signal_id"],
        schema="signals",
    )
    op.create_index(
        "ix_signal_values_instrument_id",
        "signal_values",
        ["instrument_id"],
        schema="signals",
    )
    op.create_index(
        "ix_signal_values_observation_ts",
        "signal_values",
        ["observation_ts"],
        schema="signals",
    )

    bind = op.get_bind()
    has_timescale = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    if has_timescale:
        op.execute(
            sa.text(
                "SELECT create_hypertable('signals.signal_values', 'value_ts', "
                "if_not_exists => TRUE, migrate_data => TRUE)"
            )
        )


def downgrade() -> None:
    op.drop_index(
        "ix_signal_values_observation_ts",
        table_name="signal_values",
        schema="signals",
    )
    op.drop_index(
        "ix_signal_values_instrument_id",
        table_name="signal_values",
        schema="signals",
    )
    op.drop_index(
        "ix_signal_values_signal_id", table_name="signal_values", schema="signals"
    )
    op.drop_table("signal_values", schema="signals")
