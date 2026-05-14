"""System schema models.

Houses cross-cutting infrastructure tables:

- ``methods_registry``         — every method ever registered, with current status.
- ``method_status_history``    — append-only audit log of status transitions.
- ``method_comparisons``       — one row per comparator run.
- ``heartbeat``                — pipeline liveness (written by Dagster).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from macro_trader.db.base import Base
from macro_trader.methods.status import MethodStatus

SYSTEM = "system"


class MethodRegistryRow(Base):
    """Mirror of the in-process methods registry. One row per registered method."""

    __tablename__ = "methods_registry"
    __table_args__ = (
        Index("ix_methods_registry_component", "component"),
        Index("ix_methods_registry_status", "status"),
        {"schema": SYSTEM},
    )

    method_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[MethodStatus] = mapped_column(
        SAEnum(
            MethodStatus,
            name="method_status",
            schema=SYSTEM,
            native_enum=True,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status_changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    serialized_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class MethodStatusHistoryRow(Base):
    """Append-only log of status transitions for `MethodRegistryRow`."""

    __tablename__ = "method_status_history"
    __table_args__ = (
        Index("ix_method_status_history_method_id", "method_id"),
        {"schema": SYSTEM},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(f"{SYSTEM}.methods_registry.method_id", ondelete="CASCADE"),
        nullable=False,
    )
    old_status: Mapped[MethodStatus | None] = mapped_column(
        SAEnum(
            MethodStatus,
            name="method_status",
            schema=SYSTEM,
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=True,
    )
    new_status: Mapped[MethodStatus] = mapped_column(
        SAEnum(
            MethodStatus,
            name="method_status",
            schema=SYSTEM,
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")


class MethodComparisonRow(Base):
    """One row per comparator run."""

    __tablename__ = "method_comparisons"
    __table_args__ = (
        Index("ix_method_comparisons_component", "component"),
        Index("ix_method_comparisons_methods", "method_a_id", "method_b_id"),
        {"schema": SYSTEM},
    )

    comparison_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    component: Mapped[str] = mapped_column(String(64), nullable=False)
    method_a_id: Mapped[str] = mapped_column(String(128), nullable=False)
    method_b_id: Mapped[str] = mapped_column(String(128), nullable=False)
    period_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    agreement: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    stability: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class HeartbeatRow(Base):
    """Pipeline liveness pulse, written by the Dagster heartbeat asset."""

    __tablename__ = "heartbeat"
    __table_args__ = {"schema": SYSTEM}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


# ----------------------------------------------------------------------
# Stage 2 — data infrastructure
# ----------------------------------------------------------------------
class DataSource(Base):
    """Registry of external data sources we ingest from."""

    __tablename__ = "data_sources"
    __table_args__ = {"schema": SYSTEM}

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    api_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requires_auth: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    rate_limit_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_health_check: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    is_healthy: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class DataLineage(Base):
    """One row per ingester run. Provenance for every data row we hold."""

    __tablename__ = "data_lineage"
    __table_args__ = (
        Index("ix_data_lineage_source", "source_id"),
        Index("ix_data_lineage_fetched_at", "fetched_at"),
        {"schema": SYSTEM},
    )

    lineage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{SYSTEM}.data_sources.source_id", ondelete="RESTRICT"),
        nullable=False,
    )
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    fetch_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transformation_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rows_ingested: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_updated: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_rejected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lineage_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    dagster_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dagster_asset_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class DataFreshness(Base):
    """Per-(source, table_or_series) freshness tracking."""

    __tablename__ = "data_freshness"
    __table_args__ = {"schema": SYSTEM}

    source_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(f"{SYSTEM}.data_sources.source_id", ondelete="CASCADE"),
        primary_key=True,
    )
    series_or_table: Mapped[str] = mapped_column(String(128), primary_key=True)
    expected_frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    expected_delay_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_successful_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_attempted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    is_stale: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class DataQualityFlag(Base):
    """Outcomes of a data-quality method run on a single (series, value_ts)."""

    __tablename__ = "data_quality_flags"
    __table_args__ = (
        Index("ix_dq_flags_method", "method_id"),
        Index("ix_dq_flags_series_value", "series_id", "value_ts"),
        Index("ix_dq_flags_run_at", "run_at"),
        {"schema": SYSTEM},
    )

    flag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(f"{SYSTEM}.methods_registry.method_id", ondelete="CASCADE"),
        nullable=False,
    )
    series_id: Mapped[str] = mapped_column(String(128), nullable=False)
    value_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    value: Mapped[float | None] = mapped_column(sa.Numeric(28, 10), nullable=True)
    is_flagged: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    confidence: Mapped[float | None] = mapped_column(sa.Numeric(8, 6), nullable=True)
    run_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
