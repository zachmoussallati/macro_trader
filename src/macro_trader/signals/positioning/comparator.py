"""Positioning family comparator.

Adds two family-specific metrics on top of :class:`SignalFamilyComparator`:

- ``extreme_overlap`` — when method A flags an instrument as extreme
  (|raw_value| >= the tanh of the extreme threshold), what fraction of
  the time does B flag it as extreme too?
- ``avg_history_weeks_a`` / ``_b`` — the average COT history each method
  saw across the observations in the comparison window (low values
  flag periods when neither method has enough data to be confident).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from macro_trader.signals.base import SignalFamilyComparator

# Default extreme threshold matches the methods' configured threshold of
# 2 sigma. Compare on the raw_value (tanh-squashed): tanh(2) is ~0.964.
_DEFAULT_EXTREME_VALUE = float(np.tanh(2.0))


class PositioningSignalComparator(SignalFamilyComparator):
    def __init__(self, *, extreme_value: float = _DEFAULT_EXTREME_VALUE) -> None:
        super().__init__(component="positioning_signal")
        self.extreme_value = float(extreme_value)

    def _extra_metrics(self, merged: pd.DataFrame) -> dict[str, float]:
        out: dict[str, float] = {}
        if merged.empty:
            return out

        a_extreme = merged["raw_value_a"].abs() >= self.extreme_value
        b_extreme = merged["raw_value_b"].abs() >= self.extreme_value
        a_count = int(a_extreme.sum())
        # Overlap fraction conditional on A flagging extreme — useful for
        # the dashboard's "do the two methods agree on the most actionable
        # cases?" question.
        if a_count > 0:
            out["extreme_overlap"] = float((a_extreme & b_extreme).sum() / a_count)
        else:
            out["extreme_overlap"] = 0.0

        history_a, history_b = _avg_history(merged)
        out["avg_history_weeks_a"] = history_a
        out["avg_history_weeks_b"] = history_b
        return out


def _avg_history(merged: pd.DataFrame) -> tuple[float, float]:
    """Pull the ``history_weeks`` field out of each side's stored metadata.

    The merged dataframe in ``SignalFamilyComparator._compute_metrics`` is
    indexed by (instrument_id, value_ts) with ``..._a`` / ``..._b``
    suffixes — but the framework only carries scalar columns through.
    The history_weeks values live in the underlying ``SignalOutput.metadata``
    dicts which the comparator has already dropped by the time we get
    here. The base class can be extended later to thread metadata
    through; for now we return ``nan`` and let the dashboard query
    ``signal_values.metadata`` directly when it needs the breakdown.
    """
    nan = float("nan")
    return nan, nan


__all__ = ["PositioningSignalComparator"]
