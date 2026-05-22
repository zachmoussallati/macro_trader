"""Portfolio schema models (Stage 8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from macro_trader.db.base import Base

PORTFOLIO = "portfolio"


class CovarianceEstimate(Base):
    """One row per (method, as_of, instrument_a, instrument_b).

    Stored as a TimescaleDB hypertable on ``as_of``. For each daily
    run, every pair (including the diagonal where a == b) gets a row.
    Diagonal rows hold the variance; off-diagonal hold the covariance.
    """

    __tablename__ = "covariance_estimates"
    __table_args__ = (
        Index("ix_covariance_method_asof", "method_id", "as_of"),
        {"schema": PORTFOLIO},
    )

    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
        primary_key=True,
    )
    as_of: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    instrument_a: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    instrument_b: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    covariance: Mapped[float | None] = mapped_column(Numeric(28, 14), nullable=True)
    correlation: Mapped[float | None] = mapped_column(Numeric(12, 8), nullable=True)
    lookback_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cov_metadata: Mapped[dict[str, Any]] = mapped_column(
        "cov_metadata", JSONB, nullable=False, default=dict
    )


class VolatilityEstimate(Base):
    """Per-instrument annualised volatility from the covariance run."""

    __tablename__ = "volatility_estimates"
    __table_args__ = ({"schema": PORTFOLIO},)

    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
        primary_key=True,
    )
    as_of: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    volatility: Mapped[float | None] = mapped_column(Numeric(20, 10), nullable=True)
    lookback_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vol_metadata: Mapped[dict[str, Any]] = mapped_column(
        "vol_metadata", JSONB, nullable=False, default=dict
    )


class Position(Base):
    """Sized position row — one per (method, as_of, instrument).

    ``target_weight`` is post-gate (what the system would actually
    trade). ``pre_gate_weight`` is what the portfolio method
    produced *before* the drawdown gate applied its scaling factor.
    The pair makes it possible to audit the gate's effect.
    """

    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_method_asof", "method_id", "as_of"),
        {"schema": PORTFOLIO},
    )

    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
        primary_key=True,
    )
    as_of: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_weight: Mapped[float] = mapped_column(Numeric(12, 8), nullable=False)
    pre_gate_weight: Mapped[float | None] = mapped_column(Numeric(12, 8), nullable=True)
    expected_vol_contribution: Mapped[float | None] = mapped_column(
        Numeric(12, 8), nullable=True
    )
    composite_score: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    block: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gate_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gate_scaling_factor: Mapped[float | None] = mapped_column(
        Numeric(8, 6), nullable=True
    )
    position_metadata: Mapped[dict[str, Any]] = mapped_column(
        "position_metadata", JSONB, nullable=False, default=dict
    )
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class EquityCurveRow(Base):
    """Per-method NAV / drawdown timeseries."""

    __tablename__ = "equity_curve"
    __table_args__ = ({"schema": PORTFOLIO},)

    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
        primary_key=True,
    )
    as_of: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    nav: Mapped[float] = mapped_column(Numeric(28, 14), nullable=False)
    daily_return: Mapped[float | None] = mapped_column(Numeric(20, 10), nullable=True)
    cumulative_return: Mapped[float | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    peak_nav: Mapped[float | None] = mapped_column(Numeric(28, 14), nullable=True)
    drawdown_from_peak: Mapped[float | None] = mapped_column(
        Numeric(12, 8), nullable=True
    )
    equity_metadata: Mapped[dict[str, Any]] = mapped_column(
        "equity_metadata", JSONB, nullable=False, default=dict
    )


class DrawdownStateRow(Base):
    """Single row per method capturing the current gate state."""

    __tablename__ = "drawdown_state"
    __table_args__ = ({"schema": PORTFOLIO},)

    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
        primary_key=True,
    )
    current_gate_level: Mapped[str] = mapped_column(String(16), nullable=False)
    level_1_triggered_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    level_1_release_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    level_2_triggered_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    level_2_release_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    level_3_triggered_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    effective_scaling_factor: Mapped[float] = mapped_column(
        Numeric(8, 6), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    drawdown_metadata: Mapped[dict[str, Any]] = mapped_column(
        "drawdown_metadata", JSONB, nullable=False, default=dict
    )


__all__ = [
    "CovarianceEstimate",
    "DrawdownStateRow",
    "EquityCurveRow",
    "Position",
    "VolatilityEstimate",
]
