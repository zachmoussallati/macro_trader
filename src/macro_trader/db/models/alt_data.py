"""Alt-data schema models (EIA, USDA, NOAA, Google Trends)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Index, Numeric, String
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from macro_trader.db.base import Base

ALT_DATA = "alt_data"


class EIAInventory(Base):
    """EIA petroleum + natural-gas stocks. Hypertable on `value_ts`."""

    __tablename__ = "eia_inventory"
    __table_args__ = (
        Index("ix_eia_inventory_series", "series_id"),
        {"schema": ALT_DATA},
    )

    series_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    observation_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    value: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    units: Mapped[str | None] = mapped_column(String(32), nullable=True)
    affected_instruments: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="eia")
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class USDAReport(Base):
    """USDA WASDE + NASS crop progress + grain stocks. Hypertable on `value_ts`."""

    __tablename__ = "usda_reports"
    __table_args__ = (
        Index("ix_usda_reports_report_type", "report_type"),
        Index("ix_usda_reports_commodity", "commodity"),
        {"schema": ALT_DATA},
    )

    # Composite PK: report_id is the logical identity, value_ts is part of
    # the key only because TimescaleDB requires the partition column to
    # appear in the PK of a hypertable.
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    value_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    observation_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    commodity: Mapped[str] = mapped_column(String(32), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    units: Mapped[str | None] = mapped_column(String(32), nullable=True)
    affected_instruments: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="usda")
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class WeatherData(Base):
    """NOAA HDD/CDD/precipitation/drought. Hypertable on `value_ts`."""

    __tablename__ = "weather_data"
    __table_args__ = (
        Index("ix_weather_region", "region"),
        Index("ix_weather_metric", "metric"),
        {"schema": ALT_DATA},
    )

    station_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    metric: Mapped[str] = mapped_column(String(32), primary_key=True)
    observation_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    value: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    affected_instruments: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="noaa")
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class GoogleTrends(Base):
    """Google Trends search-interest indices. Hypertable on `value_ts`."""

    __tablename__ = "google_trends"
    __table_args__ = (
        Index("ix_google_trends_query", "query_term"),
        {"schema": ALT_DATA},
    )

    query_term: Mapped[str] = mapped_column(String(128), primary_key=True)
    region: Mapped[str] = mapped_column(String(8), primary_key=True)
    value_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    observation_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    value: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    affected_instruments: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="google_trends")
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
