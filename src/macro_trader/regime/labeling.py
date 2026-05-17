"""Centroid-anchored regime labeling.

When HMM / GMM / MS-VAR refits weekly, it produces K=5 latent
states. These have no inherent ordering — state 0 might be
``risk_on_growth`` this week and ``vol_spike`` next week. This
module produces stable named labels:

1. On first ever fit, the operator (or a config) provides an
   *anchor* centroid table — one centroid per named regime,
   defined as the expected feature-vector mean for that regime
   (e.g. ``stagflation`` has high inflation + low growth + neutral
   vol). The fitted centroids get matched to the anchors by the
   Hungarian algorithm and labels are inherited.

2. On every subsequent refit, the new fitted centroids get matched
   to the *prior fit's* centroids (which already carry their named
   labels). Same Hungarian algorithm.

3. Centroid distance is L2 in feature-z-score space. The factor
   panel ships z-scored already; we don't normalise again.

4. If a centroid moves >2 std from the matched prior centroid, log
   a ``label_drift_warning`` — the regime structure may have
   genuinely shifted (e.g. a new monetary regime).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from scipy.optimize import linear_sum_assignment

from macro_trader.logging_setup import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


# Anchor centroids: feature-vector means for each named regime.
# Columns match REGIME_FEATURE_COLUMNS. Values are in z-score space
# for factors 1-6 and rough absolute levels for the rest. These are
# rough priors used only on first-ever fit — subsequent refits use
# prior fitted centroids as the anchor.
DEFAULT_ANCHOR_CENTROIDS: dict[str, dict[str, float]] = {
    "risk_on_growth": {
        "growth": 0.8,
        "inflation": 0.0,
        "liquidity": 0.3,
        "usd": -0.3,
        "oil": 0.3,
        "risk_on": 0.8,
        "realized_vol_60d": 0.12,
        "credit_spread": 0.0,
        "yield_curve_slope": 1.5,
        "vix_level": 13.0,
    },
    "risk_off_defensive": {
        "growth": -0.5,
        "inflation": 0.0,
        "liquidity": -0.3,
        "usd": 0.7,
        "oil": -0.3,
        "risk_on": -0.7,
        "realized_vol_60d": 0.22,
        "credit_spread": 0.02,
        "yield_curve_slope": 0.5,
        "vix_level": 24.0,
    },
    "stagflation": {
        "growth": -0.5,
        "inflation": 1.2,
        "liquidity": -0.8,
        "usd": 0.3,
        "oil": 0.5,
        "risk_on": -0.3,
        "realized_vol_60d": 0.18,
        "credit_spread": 0.015,
        "yield_curve_slope": 0.0,
        "vix_level": 22.0,
    },
    "carry_friendly": {
        "growth": 0.2,
        "inflation": -0.2,
        "liquidity": 0.2,
        "usd": 0.0,
        "oil": 0.0,
        "risk_on": 0.4,
        "realized_vol_60d": 0.10,
        "credit_spread": -0.005,
        "yield_curve_slope": 1.0,
        "vix_level": 14.0,
    },
    "vol_spike": {
        "growth": -0.8,
        "inflation": 0.2,
        "liquidity": -0.6,
        "usd": 0.5,
        "oil": -0.5,
        "risk_on": -1.0,
        "realized_vol_60d": 0.35,
        "credit_spread": 0.05,
        "yield_curve_slope": -0.5,
        "vix_level": 35.0,
    },
}


def anchor_centroids_matrix(
    feature_columns: list[str], named_regimes: list[str]
) -> np.ndarray:
    """Build an ``(n_named_regimes, n_features)`` anchor matrix from
    ``DEFAULT_ANCHOR_CENTROIDS`` aligned to the given feature columns.
    Features without a default value default to 0."""
    out = np.zeros((len(named_regimes), len(feature_columns)))
    for i, regime in enumerate(named_regimes):
        anchor = DEFAULT_ANCHOR_CENTROIDS.get(regime, {})
        for j, col in enumerate(feature_columns):
            out[i, j] = anchor.get(col, 0.0)
    return out


def map_centroids_to_labels(
    new_centroids: np.ndarray,
    prior_centroids: np.ndarray,
    prior_labels: list[str],
    *,
    drift_warning_threshold: float = 2.0,
) -> tuple[list[str], dict[str, object]]:
    """Match ``new_centroids`` to ``prior_labels`` via the Hungarian
    algorithm minimising L2 distance per row.

    Returns ``(new_labels_in_row_order, mapping_summary)``. The
    mapping summary carries the assignment cost matrix + a
    ``drift_warnings`` list naming any new centroid that moved more
    than ``drift_warning_threshold`` from its matched prior centroid.
    """
    if new_centroids.shape[0] != len(prior_labels):
        raise ValueError(
            f"new_centroids has {new_centroids.shape[0]} rows but "
            f"prior_labels has {len(prior_labels)}"
        )
    if new_centroids.shape[1] != prior_centroids.shape[1]:
        raise ValueError("feature-dimensions must match")

    cost = np.linalg.norm(
        new_centroids[:, None, :] - prior_centroids[None, :, :], axis=-1
    )
    row_ind, col_ind = linear_sum_assignment(cost)
    new_labels: list[str] = [""] * len(row_ind)
    drift_warnings: list[dict[str, object]] = []
    for r, c in zip(row_ind, col_ind, strict=True):
        new_labels[r] = prior_labels[c]
        if cost[r, c] > drift_warning_threshold:
            drift_warnings.append(
                {
                    "new_state_index": int(r),
                    "matched_label": prior_labels[c],
                    "distance": float(cost[r, c]),
                }
            )
            log.warning(
                "regime.labeling.drift",
                label=prior_labels[c],
                distance=float(cost[r, c]),
            )

    summary: dict[str, object] = {
        "cost_matrix": cost.tolist(),
        "assignment": list(zip([int(x) for x in row_ind], [int(x) for x in col_ind], strict=True)),
        "drift_warnings": drift_warnings,
    }
    return new_labels, summary


__all__ = [
    "DEFAULT_ANCHOR_CENTROIDS",
    "anchor_centroids_matrix",
    "map_centroids_to_labels",
]
