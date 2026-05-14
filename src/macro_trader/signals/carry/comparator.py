"""Carry comparator — inactive in Stage 3 (only one method exists).

Wired so the framework + dashboard handle it identically to the other
families. When Stage 12 introduces a proper futures-based enhancement,
this becomes active without code-shape changes.
"""

from __future__ import annotations

from macro_trader.signals.base import SignalFamilyComparator


class CarrySignalComparator(SignalFamilyComparator):
    def __init__(self) -> None:
        super().__init__(component="carry_signal")
