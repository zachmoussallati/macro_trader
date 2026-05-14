"""Macro-data + calendar schema models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from macro_trader.db.base import Base

MACRO_DATA = "macro_data"


class Series(Base):
    """Macro series registry. One row per series_id (e.g. ``FRED:GDPC1``)."""

    __tablename__ = "series"
    __table_args__ = (
        Index("ix_series_source", "source"),
        Index("ix_series_category", "category"),
        Index("ix_series_is_active", "is_active"),
        {"schema": MACRO_DATA},
    )

    series_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    units: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seasonal_adjustment: Mapped[str | None] = mapped_column(String(8), nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    affected_instruments: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    series_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class SeriesObservation(Base):
    """Series observations with FRED-style vintage handling.

    PK = ``(series_id, value_ts, observation_ts)`` so each vintage gets its
    own row. ``realtime_start`` / ``realtime_end`` mirror FRED's ALFRED
    convention. ``is_initial = True`` for the first release.
    """

    __tablename__ = "series_observations"
    __table_args__ = (
        Index("ix_series_obs_series_value", "series_id", "value_ts"),
        Index("ix_series_obs_realtime", "realtime_start", "realtime_end"),
        {"schema": MACRO_DATA},
    )

    series_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey(f"{MACRO_DATA}.series.series_id", ondelete="CASCADE"),
        primary_key=True,
    )
    value_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    observation_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    value: Mapped[float | None] = mapped_column(Numeric(28, 10), nullable=True)
    realtime_start: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    realtime_end: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    is_initial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class CalendarEvent(Base):
    """Calendar events (data releases, central-bank decisions, geopolitical)."""

    __tablename__ = "calendar_events"
    __table_args__ = (
        Index("ix_calendar_events_event_ts", "event_ts"),
        Index("ix_calendar_events_kind", "kind"),
        Index("ix_calendar_events_importance", "importance"),
        {"schema": MACRO_DATA},
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    actual_release_ts: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    region: Mapped[str | None] = mapped_column(String(16), nullable=True)
    importance: Mapped[str] = mapped_column(String(16), nullable=False)
    affected_instruments: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    affected_series: Mapped[list[str]] = mapped_column(
        ARRAY(String(128)), nullable=False, default=list
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
