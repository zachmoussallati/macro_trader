"""Catalyst family comparator."""

from __future__ import annotations

import pandas as pd

from macro_trader.signals.base import SignalFamilyComparator


class CatalystSignalComparator(SignalFamilyComparator):
    def __init__(self) -> None:
        super().__init__(component="catalyst_signal")

    def _extra_metrics(self, merged: pd.DataFrame) -> dict[str, float]:
        if merged.empty:
            return {}
        out: dict[str, float] = {
            "value_correlation": float(merged["raw_value_a"].corr(merged["raw_value_b"])),
        }
        return out


__all__ = ["CatalystSignalComparator"]
