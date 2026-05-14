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

from sqlalchemy import (
    BigInteger,
    Enum as SAEnum,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
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
        SAEnum(MethodStatus, name="method_status", schema=SYSTEM, native_enum=True),
        nullable=False,
    )
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status_changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    status_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    serialized_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class MethodStatusHistoryRow(Base):
    """Append-only log of status transitions for `MethodRegistryRow`."""

    __tablename__ = "method_status_history"
    __table_args__ = (
        Index("ix_method_status_history_method_id", "method_id"),
        {"schema": SYSTEM},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(f"{SYSTEM}.methods_registry.method_id", ondelete="CASCADE"),
        nullable=False,
    )
    old_status: Mapped[MethodStatus | None] = mapped_column(
        SAEnum(MethodStatus, name="method_status", schema=SYSTEM, create_type=False),
        nullable=True,
    )
    new_status: Mapped[MethodStatus] = mapped_column(
        SAEnum(MethodStatus, name="method_status", schema=SYSTEM, create_type=False),
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
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    agreement: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    stability: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class HeartbeatRow(Base):
    """Pipeline liveness pulse, written by the Dagster heartbeat asset."""

    __tablename__ = "heartbeat"
    __table_args__ = {"schema": SYSTEM}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
