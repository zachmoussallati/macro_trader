"""Signals schema models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from macro_trader.db.base import Base

SIGNALS = "signals"


class SignalValue(Base):
    """One row per (signal_id, instrument_id, value_ts, observation_ts).

    Stored as a TimescaleDB hypertable on ``value_ts`` so per-instrument
    historical queries stay fast as the table grows. Point-in-time
    discipline: queries pin a vintage by filtering ``observation_ts <=
    as_of``.

    Four output fields per row let downstream consumers (composite
    scoring in Stage 7, dashboards) pick the representation they need
    without recomputing:

    - ``raw_value``  — direct signal output, signal-specific units
    - ``zscore``     — standardised, comparable across signals
    - ``rank``       — cross-sectional rank in [0, 1] within the universe
    - ``confidence`` — in [0, 1]; low values are de-weighted in composites

    Plus an in-sample ``rolling_sharpe_252`` to track signal decay; Stage 9
    replaces this with proper walk-forward Sharpe.
    """

    __tablename__ = "signal_values"
    __table_args__ = (
        Index("ix_signal_values_signal_id", "signal_id"),
        Index("ix_signal_values_instrument_id", "instrument_id"),
        Index("ix_signal_values_observation_ts", "observation_ts"),
        {"schema": SIGNALS},
    )

    signal_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
        primary_key=True,
    )
    instrument_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    value_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    observation_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    raw_value: Mapped[float | None] = mapped_column(Numeric(28, 10), nullable=True)
    zscore: Mapped[float | None] = mapped_column(Numeric(20, 10), nullable=True)
    rank: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    rolling_sharpe_252: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    signal_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class CompositeScore(Base):
    """One row per (composite_method, instrument, value_ts, observation_ts).

    Stage 7 composite scoring output. The ``score`` column is the
    final tanh-squashed signed score in ``[-1, 1]``; ``raw_score``
    keeps the pre-squash linear combination for diagnostics. The
    ``composite_metadata`` JSONB carries per-signal contributions
    (signal_id -> {weight, z, contribution}) so the dashboard can
    decompose any score back to its inputs without recomputing.
    """

    __tablename__ = "composite_scores"
    __table_args__ = (
        Index("ix_composite_scores_method_id", "method_id"),
        Index("ix_composite_scores_instrument_id", "instrument_id"),
        Index("ix_composite_scores_observation_ts", "observation_ts"),
        {"schema": SIGNALS},
    )

    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
        primary_key=True,
    )
    instrument_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    value_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    observation_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    raw_score: Mapped[float | None] = mapped_column(Numeric(20, 10), nullable=True)
    score: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    regime_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    n_signals_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    composite_metadata: Mapped[dict[str, Any]] = mapped_column(
        "composite_metadata", JSONB, nullable=False, default=dict
    )
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class CompositeWeight(Base):
    """Versioned weight snapshot.

    One row per (composite_method, snapshot_ts, regime_label,
    signal_method). Snapshots are written weekly after attribution
    refresh; daily scoring reads the most-recent snapshot at or
    before ``as_of`` so weights are queryable point-in-time.
    """

    __tablename__ = "composite_weights"
    __table_args__ = (
        Index("ix_composite_weights_method_snapshot", "method_id", "snapshot_ts"),
        {"schema": SIGNALS},
    )

    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
        primary_key=True,
    )
    snapshot_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    regime_label: Mapped[str] = mapped_column(String(64), primary_key=True)
    signal_method_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    weight: Mapped[float] = mapped_column(Numeric(12, 8), nullable=False)
    weight_source: Mapped[str] = mapped_column(String(32), nullable=False)
    weight_metadata: Mapped[dict[str, Any]] = mapped_column(
        "weight_metadata", JSONB, nullable=False, default=dict
    )


__all__ = ["CompositeScore", "CompositeWeight", "SignalValue"]
