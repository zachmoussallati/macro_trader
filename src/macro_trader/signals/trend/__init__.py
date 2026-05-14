"""Trend family — Stage 3."""

from macro_trader.signals.trend.comparator import TrendSignalComparator
from macro_trader.signals.trend.methods import (
    HPFilterTrend,
    SMACrossoverLong,
    SMACrossoverMedium,
    SMACrossoverShort,
    TrendEnsemble,
)

__all__ = [
    "HPFilterTrend",
    "SMACrossoverLong",
    "SMACrossoverMedium",
    "SMACrossoverShort",
    "TrendEnsemble",
    "TrendSignalComparator",
]
