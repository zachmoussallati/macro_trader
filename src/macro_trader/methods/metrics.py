"""Generic metric helpers for method comparison.

These helpers are intentionally permissive about input types: they accept
anything that can be coerced to a 1-D numpy array. Component-specific
comparators may override the calls in their `_compute_*` hooks if they need
shape-aware behaviour.
"""

from __future__ import annotations

from collections.abc import Iterable, Sized
from typing import Any

import numpy as np
from scipy import stats

from macro_trader.methods.base import Method


def _to_array(x: Any) -> np.ndarray | None:
    """Best-effort numeric coercion. Returns None if not coercible."""
    if x is None:
        return None
    try:
        arr = np.asarray(x, dtype=float)
    except (TypeError, ValueError):
        return None
    if arr.ndim == 0:
        return arr.reshape(1)
    return arr.ravel()


def _exact_match_rate(a: Any, b: Any) -> float | None:
    """For categorical outputs (e.g. regime labels): fraction of positions
    where the two are equal. Returns None if shapes/types are incompatible."""
    if not isinstance(a, Sized) or not isinstance(b, Sized):
        return None
    if len(a) != len(b) or len(a) == 0:
        return None
    if isinstance(a, np.ndarray) and isinstance(b, np.ndarray):
        return float(np.mean(a == b))
    if isinstance(a, Iterable) and isinstance(b, Iterable):
        eq = sum(1 for x, y in zip(a, b, strict=True) if x == y)
        return eq / len(a)
    return None


def output_agreement(output_a: Any, output_b: Any) -> dict[str, float]:
    """Generic agreement statistics between two methods' outputs.

    Returns whichever of the following metrics are computable for the inputs:
      - ``pearson_correlation``
      - ``spearman_rank_correlation``
      - ``exact_match_rate`` (for categorical / label outputs)
      - ``mae`` (mean absolute error, treating ``a`` as reference)
      - ``rmse``
    """
    out: dict[str, float] = {}

    # Categorical / label-style outputs.
    em = _exact_match_rate(output_a, output_b)
    if em is not None:
        out["exact_match_rate"] = em

    # Numeric outputs.
    arr_a = _to_array(output_a)
    arr_b = _to_array(output_b)
    if arr_a is None or arr_b is None or arr_a.size != arr_b.size or arr_a.size < 2:
        return out

    # Strip NaNs jointly.
    mask = ~(np.isnan(arr_a) | np.isnan(arr_b))
    a = arr_a[mask]
    b = arr_b[mask]
    if a.size < 2:
        return out

    if np.std(a) > 0 and np.std(b) > 0:
        pearson = float(np.corrcoef(a, b)[0, 1])
        out["pearson_correlation"] = pearson
        rho, _ = stats.spearmanr(a, b)
        if rho is not None and not np.isnan(rho):
            out["spearman_rank_correlation"] = float(rho)

    out["mae"] = float(np.mean(np.abs(a - b)))
    out["rmse"] = float(np.sqrt(np.mean((a - b) ** 2)))
    return out


def output_stability(
    method_a: Method[Any, Any],
    method_b: Method[Any, Any],
    data: Any,
    *,
    n_perturbations: int = 4,
    noise_scale: float = 1e-6,
) -> dict[str, float]:
    """Cheap stability proxy: re-run each method on a tiny perturbation of the
    input and measure how much the output moves.

    Intended for numeric inputs where injecting epsilon-scale noise is well
    defined. For non-numeric inputs, returns an empty dict — component-specific
    comparators should provide their own ``_compute_stability``.
    """
    arr = _to_array(data)
    if arr is None or arr.size == 0:
        return {}

    rng = np.random.default_rng(seed=0)
    deltas_a: list[float] = []
    deltas_b: list[float] = []

    base_a = _to_array(method_a.predict(data))
    base_b = _to_array(method_b.predict(data))
    if base_a is None or base_b is None:
        return {}

    for _ in range(n_perturbations):
        noise = rng.normal(scale=noise_scale, size=arr.size)
        try:
            perturbed = (arr + noise).reshape(np.asarray(data).shape)
        except (TypeError, ValueError):
            perturbed = arr + noise
        try:
            out_a = _to_array(method_a.predict(perturbed))
            out_b = _to_array(method_b.predict(perturbed))
        except Exception:
            continue
        if out_a is None or out_b is None:
            continue
        if out_a.size == base_a.size:
            deltas_a.append(float(np.linalg.norm(out_a - base_a)))
        if out_b.size == base_b.size:
            deltas_b.append(float(np.linalg.norm(out_b - base_b)))

    result: dict[str, float] = {}
    if deltas_a:
        result["mean_perturbation_drift_a"] = float(np.mean(deltas_a))
    if deltas_b:
        result["mean_perturbation_drift_b"] = float(np.mean(deltas_b))
    return result
