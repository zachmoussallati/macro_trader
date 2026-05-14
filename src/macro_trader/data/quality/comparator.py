"""Component-specific comparator for data quality.

Reports flag rates, Intersection-over-Union, correlation between the two
methods' bool masks, and the count of observations the comparison ran on.
"""

from __future__ import annotations

import numpy as np

from macro_trader.methods.comparator import MethodComparator


class DataQualityComparator(MethodComparator[np.ndarray, np.ndarray]):
    """Compares two ``data_quality`` methods on identical series inputs."""

    def __init__(self) -> None:
        super().__init__(component="data_quality")

    def _compute_metrics(
        self,
        output_a: np.ndarray,
        output_b: np.ndarray,
        *,
        data: np.ndarray,
    ) -> dict[str, float]:
        a = np.asarray(output_a, dtype=bool)
        b = np.asarray(output_b, dtype=bool)
        n = int(min(a.size, b.size))
        if n == 0:
            return {
                "flag_rate_a": 0.0,
                "flag_rate_b": 0.0,
                "agreement_iou": 0.0,
                "agreement_correlation": 0.0,
                "n_observations": 0.0,
            }
        a = a[:n]
        b = b[:n]
        intersection = float(np.logical_and(a, b).sum())
        union = float(np.logical_or(a, b).sum())
        iou = (intersection / union) if union > 0 else 1.0  # both all-False → trivially agree
        # Pearson correlation on the 0/1 vectors.
        a_int = a.astype(int)
        b_int = b.astype(int)
        if a_int.std() == 0 or b_int.std() == 0:
            correlation = 1.0 if (a_int == b_int).all() else 0.0
        else:
            correlation = float(np.corrcoef(a_int, b_int)[0, 1])
        return {
            "flag_rate_a": float(a.mean()),
            "flag_rate_b": float(b.mean()),
            "agreement_iou": float(iou),
            "agreement_correlation": correlation,
            "n_observations": float(n),
        }
