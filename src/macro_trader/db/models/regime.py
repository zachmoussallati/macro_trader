"""Regime-classifier schema models (Stage 6).

Two hypertables on ``value_ts``:

- ``regime.regime_states`` — daily classification per regime method.
- ``regime.regime_attribution`` — weekly per-regime-per-signal
  performance summary; Stage 7 composite scoring reads this to
  weight signals by regime.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from macro_trader.db.base import Base

REGIME = "regime"


class RegimeState(Base):
    """One daily classification per (regime method, value_ts)."""

    __tablename__ = "regime_states"
    __table_args__ = (
        Index("ix_regime_states_label", "label"),
        {"schema": REGIME},
    )

    method_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("system.methods_registry.method_id", ondelete="CASCADE"),
        primary_key=True,
    )
    value_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    observation_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    probability_vector: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    confidence: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    transition_prob: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    days_in_regime: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state_metadata: Mapped[dict[str, Any]] = mapped_column(
        "state_metadata", JSONB, nullable=False, default=dict
    )
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class RegimeAttribution(Base):
    """Per-regime per-signal-method historical performance metric.

    Composite PK is (regime_method_id, regime_label, signal_method_id,
    value_ts). One row summarises a (regime, signal) bucket over the
    attribution lookback window ending at ``value_ts``.
    """

    __tablename__ = "regime_attribution"
    __table_args__ = ({"schema": REGIME},)

    regime_method_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    regime_label: Mapped[str] = mapped_column(String(64), primary_key=True)
    signal_method_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    n_observations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mean_return: Mapped[float | None] = mapped_column(Numeric(20, 10), nullable=True)
    sharpe: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    hit_rate: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    attribution_metadata: Mapped[dict[str, Any]] = mapped_column(
        "attribution_metadata", JSONB, nullable=False, default=dict
    )


__all__ = ["RegimeAttribution", "RegimeState"]
