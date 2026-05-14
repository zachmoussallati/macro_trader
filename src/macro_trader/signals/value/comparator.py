"""Value comparator."""

from __future__ import annotations

from macro_trader.signals.base import SignalFamilyComparator


class ValueSignalComparator(SignalFamilyComparator):
    def __init__(self) -> None:
        super().__init__(component="value_signal")
