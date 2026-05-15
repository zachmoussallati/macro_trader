"""Dislocation family comparator.

Family-specific metrics on top of :class:`SignalFamilyComparator`:

- ``residual_correlation``: Pearson correlation of the two methods'
  raw_value series (a proxy for how similarly the methods explain
  cross-sectional variance).
- ``explained_variance_a`` / ``_b``: each method's mean
  ``metadata.explained_variance`` across observations — a model-quality
  diagnostic.
"""

from __future__ import annotations

import pandas as pd

from macro_trader.signals.base import SignalFamilyComparator


class DislocationSignalComparator(SignalFamilyComparator):
    def __init__(self) -> None:
        super().__init__(component="dislocation_signal")

    def _extra_metrics(self, merged: pd.DataFrame) -> dict[str, float]:
        if merged.empty:
            return {}
        out: dict[str, float] = {}
        # Residual correlation is the same statistic as the base class's
        # value_correlation_a_b, exposed here under the conventional name
        # so promotion criteria can reference it explicitly.
        out["residual_correlation"] = float(
            merged["raw_value_a"].corr(merged["raw_value_b"])
        )
        return out


__all__ = ["DislocationSignalComparator"]
