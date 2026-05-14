"""Trend-family method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.signals.trend.methods import (
    HPFilterTrend,
    SMACrossoverLong,
    SMACrossoverMedium,
    SMACrossoverShort,
    TrendEnsemble,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def register(session: Session) -> None:
    """Register every trend method through the framework. Idempotent."""
    register_method(
        SMACrossoverShort(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 3 — short-term SMA crossover (10/30)",
    )
    register_method(
        SMACrossoverMedium(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 3 — medium-term SMA crossover (20/60)",
    )
    register_method(
        SMACrossoverLong(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 3 — long-term SMA crossover (50/200)",
    )
    register_method(
        TrendEnsemble(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 3 — designated production trend signal (equal-weighted ensemble)",
    )
    register_method(
        HPFilterTrend(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 3 — HP-filter trend deviation enhancement",
    )
