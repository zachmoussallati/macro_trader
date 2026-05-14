"""Comparator for the trend signal family."""

from __future__ import annotations

from macro_trader.signals.base import SignalFamilyComparator


class TrendSignalComparator(SignalFamilyComparator):
    """Compares two trend methods on the same SignalInput."""

    def __init__(self) -> None:
        super().__init__(component="trend_signal")
