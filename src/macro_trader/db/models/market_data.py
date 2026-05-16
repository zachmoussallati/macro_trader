"""Market-data schema models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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

MARKET_DATA = "market_data"


class Instrument(Base):
    """Symbol master. Maps our internal id (e.g. ``CL``) to ETF proxy +
    eventual paid-data target."""

    __tablename__ = "instruments"
    __table_args__ = (
        Index("ix_instruments_asset_class", "asset_class"),
        Index("ix_instruments_is_active", "is_active"),
        {"schema": MARKET_DATA},
    )

    instrument_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)
    sub_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proxy_ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    proxy_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    underlying_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    exchange: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tracking_error_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    instrument_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class DailyBar(Base):
    """OHLCV daily bars. Hypertable on `value_ts`. Point-in-time via
    `observation_ts` and `is_revised`."""

    __tablename__ = "daily_bars"
    __table_args__ = (
        Index("ix_daily_bars_instrument", "instrument_id"),
        Index("ix_daily_bars_observation_ts", "observation_ts"),
        {"schema": MARKET_DATA},
    )

    instrument_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey(f"{MARKET_DATA}.instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    value_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    observation_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    open: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    high: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    low: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    close: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    volume: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    adjusted_close: Mapped[float | None] = mapped_column(Numeric(20, 8), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_revised: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class OptionsChain(Base):
    """Row-per-strike options chain snapshot (Stage 5 vol surface).

    Hypertable on ``snapshot_ts``. Composite PK across
    ``(instrument_id, snapshot_ts, expiry_ts, strike, option_type)``
    matches the natural identity of one option contract at a given
    snapshot.
    """

    __tablename__ = "options_chains"
    __table_args__ = (
        Index(
            "ix_options_chains_instrument_expiry",
            "instrument_id",
            "expiry_ts",
        ),
        {"schema": MARKET_DATA},
    )

    instrument_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey(f"{MARKET_DATA}.instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    snapshot_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    expiry_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    strike: Mapped[float] = mapped_column(Numeric(20, 6), primary_key=True)
    option_type: Mapped[str] = mapped_column(String(8), primary_key=True)
    bid: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    ask: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    last: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    implied_vol: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    delta: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    gamma: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    vega: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    theta: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    underlying_price: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class OptionsSurface(Base):
    """Per-(instrument, method) fitted vol surface (Stage 5).

    Hypertable on ``snapshot_ts``. ``parameters`` carries the
    method-specific fit output (SVI per-slice params, spline
    knots + values, etc.); ``surface_metadata`` carries diagnostic
    fields (fit quality, n_strikes_used, etc.).
    """

    __tablename__ = "options_surfaces"
    __table_args__ = ({"schema": MARKET_DATA},)

    instrument_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey(f"{MARKET_DATA}.instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    snapshot_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    method: Mapped[str] = mapped_column(String(32), primary_key=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Python attribute is ``surface_metadata`` because ``metadata`` is reserved
    # by SQLAlchemy's Base.metadata.
    surface_metadata: Mapped[dict[str, Any]] = mapped_column(
        "surface_metadata", JSONB, nullable=False, default=dict
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
