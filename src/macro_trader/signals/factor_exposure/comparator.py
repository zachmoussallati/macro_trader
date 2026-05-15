"""Factor exposure family comparator.

Three-way comparison: OLS baseline vs RF shadow vs Causal Forest
shadow. Stage 4A's ``run_comparisons_for_component`` runner produces
two ``ComparisonResult`` rows per run: ``(ols, rf)`` and
``(ols, causal_forest)``.

Family-specific metric:

- ``loading_correlation``: how similar the factor loadings are
  between methods. OLS, RF, and CF can all produce similar return
  signals via very different exposures — comparing loadings tells you
  whether the methods "see" the same factor structure or just happen
  to agree on direction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from macro_trader.signals.base import SignalFamilyComparator


class FactorExposureComparator(SignalFamilyComparator):
    def __init__(self) -> None:
        super().__init__(component="factor_exposure_signal")

    def _extra_metrics(self, merged: pd.DataFrame) -> dict[str, float]:
        if merged.empty:
            return {}
        # The merged frame doesn't carry SignalOutput.metadata after the
        # base class flattens, so loading_correlation is a placeholder
        # awaiting the metadata-flow-through fix tracked in
        # Stage 4A tradeoffs.md (item 3). We still expose value
        # correlation under the conventional name so promotion criteria
        # can target it.
        out: dict[str, float] = {
            "value_correlation": float(
                merged["raw_value_a"].corr(merged["raw_value_b"])
            ),
        }
        if "rank_correlation_a_b" in merged.columns:
            out["rank_correlation"] = float(merged["rank_correlation_a_b"].iloc[0])
        return out


__all__ = ["FactorExposureComparator"]
_ = np  # mypy: keep numpy import alive for the placeholder
