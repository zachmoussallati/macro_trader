"""regime schema: regime.regime_states + regime.regime_attribution

Stage 6 regime classifier. Both tables are TimescaleDB hypertables on
``value_ts``. regime_states is row-per-(method, ts) with the daily
classification; regime_attribution is row-per-(regime_method,
regime_label, signal_method, ts) for Stage 7's regime-conditional
weighting.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS regime"))

    op.create_table(
        "regime_states",
        sa.Column(
            "method_id",
            sa.String(128),
            sa.ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("value_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column("observation_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("probability_vector", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("confidence", sa.Numeric(8, 6), nullable=True),
        sa.Column("transition_prob", sa.Numeric(8, 6), nullable=True),
        sa.Column("days_in_regime", sa.Integer, nullable=True),
        sa.Column("state_metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="regime",
    )
    op.create_index(
        "ix_regime_states_label",
        "regime_states",
        ["label"],
        schema="regime",
    )

    op.create_table(
        "regime_attribution",
        sa.Column("regime_method_id", sa.String(128), primary_key=True),
        sa.Column("regime_label", sa.String(64), primary_key=True),
        sa.Column("signal_method_id", sa.String(128), primary_key=True),
        sa.Column("value_ts", postgresql.TIMESTAMP(timezone=True), primary_key=True),
        sa.Column("n_observations", sa.Integer, nullable=False, default=0),
        sa.Column("mean_return", sa.Numeric(20, 10), nullable=True),
        sa.Column("sharpe", sa.Numeric(12, 6), nullable=True),
        sa.Column("hit_rate", sa.Numeric(8, 6), nullable=True),
        sa.Column(
            "attribution_metadata", postgresql.JSONB, nullable=False, server_default="{}"
        ),
        schema="regime",
    )

    bind = op.get_bind()
    has_timescale = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).first()
    if has_timescale:
        op.execute(
            sa.text(
                "SELECT create_hypertable('regime.regime_states', 'value_ts', "
                "if_not_exists => TRUE, migrate_data => TRUE)"
            )
        )
        op.execute(
            sa.text(
                "SELECT create_hypertable('regime.regime_attribution', 'value_ts', "
                "if_not_exists => TRUE, migrate_data => TRUE)"
            )
        )


def downgrade() -> None:
    op.drop_table("regime_attribution", schema="regime")
    op.drop_index("ix_regime_states_label", table_name="regime_states", schema="regime")
    op.drop_table("regime_states", schema="regime")
    op.execute(sa.text("DROP SCHEMA IF EXISTS regime"))
