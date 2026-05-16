"""Per-slice vol-surface fitting.

Stage 5 prompt's explicit fallback option: "cubic spline
interpolation across log-moneyness per expiry slice, with the
calendar arbitrage check". We take that fallback. The full Gatheral
SVI parameterization is documented in
``notes/stage_5/decisions.md`` as the path to revisit when a paid
options-data source is added (full SVI is more useful with the
deeper strikes/expiries paid data brings).

The output of :func:`fit_slice` is a :class:`SliceFit` dataclass
storing the spline knots + values for round-trip via JSON. Calendar
arbitrage (total variance must be monotonic non-decreasing in
expiry) is enforced as a post-fit check across slices in
:func:`check_calendar_arbitrage`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.interpolate import CubicSpline


@dataclass(slots=True)
class SliceFit:
    """Per-expiry-slice fitted surface."""

    expiry_dte: int          # days to expiry
    log_moneyness: list[float]
    implied_var: list[float]  # total variance w = sigma^2 * t
    n_strikes: int
    fit_rmse: float           # RMS error between input IV and fitted IV at the input strikes


def fit_slice(
    *,
    expiry_dte: int,
    log_moneyness: np.ndarray,
    iv: np.ndarray,
    min_strikes: int = 5,
) -> SliceFit | None:
    """Fit a cubic-spline interpolation across log-moneyness.

    Stores total variance (sigma^2 * t) at the input knots. The
    spline is evaluated lazily; :func:`predict_slice` rebuilds the
    CubicSpline from the stored knots + values.

    Returns ``None`` if fewer than ``min_strikes`` valid (k, iv)
    pairs are available.
    """
    if expiry_dte <= 0:
        return None
    k = np.asarray(log_moneyness, dtype=float)
    sigma = np.asarray(iv, dtype=float)
    mask = np.isfinite(k) & np.isfinite(sigma) & (sigma > 0)
    if mask.sum() < min_strikes:
        return None
    k = k[mask]
    sigma = sigma[mask]
    # Sort by log-moneyness so the spline domain is monotonic; dedup
    # by averaging on collisions.
    order = np.argsort(k)
    k_sorted = k[order]
    sigma_sorted = sigma[order]
    # Average duplicate strikes.
    unique_k, inverse = np.unique(k_sorted, return_inverse=True)
    if len(unique_k) < min_strikes:
        return None
    sigma_avg = np.zeros_like(unique_k)
    counts = np.zeros_like(unique_k)
    for i, idx in enumerate(inverse):
        sigma_avg[idx] += sigma_sorted[i]
        counts[idx] += 1
    sigma_avg /= counts

    t = expiry_dte / 365.0
    total_var = sigma_avg * sigma_avg * t

    # Fit RMSE = identity here since we interpolate at the knots.
    # Future SVI swap will produce a non-zero RMSE for noisy slices.
    fit_rmse = 0.0

    return SliceFit(
        expiry_dte=expiry_dte,
        log_moneyness=unique_k.tolist(),
        implied_var=total_var.tolist(),
        n_strikes=len(unique_k),
        fit_rmse=fit_rmse,
    )


def predict_slice(fit: SliceFit, log_moneyness: np.ndarray | float) -> np.ndarray:
    """Evaluate the slice's spline at the given log-moneyness values.

    Returns total variance ``w(k) = sigma^2 * t``. To recover IV
    divide by ``t = dte / 365`` and take ``sqrt``.
    """
    k = np.atleast_1d(np.asarray(log_moneyness, dtype=float))
    knots = np.asarray(fit.log_moneyness, dtype=float)
    values = np.asarray(fit.implied_var, dtype=float)
    if len(knots) < 2:
        return np.full_like(k, np.nan)
    spline = CubicSpline(knots, values, extrapolate=True, bc_type="natural")
    out = spline(k)
    # Clip to non-negative (total variance can't be < 0; the spline
    # might overshoot in extrapolation).
    clipped: np.ndarray = np.clip(out, 0.0, None)
    return clipped


def atm_iv(fit: SliceFit) -> float:
    """ATM implied vol from the fitted slice (k=0)."""
    w0 = float(predict_slice(fit, 0.0)[0])
    t = max(fit.expiry_dte / 365.0, 1e-9)
    if w0 <= 0:
        return float("nan")
    return float(np.sqrt(w0 / t))


def delta_iv(fit: SliceFit, *, delta: float = 0.25) -> tuple[float, float]:
    """IV at the +/- delta strikes via simple log-moneyness offsets.

    Cheap proxy: log-moneyness offset ~ |Phi^-1(delta)| * sigma_atm
    * sqrt(t). Returns (put_iv, call_iv).
    """
    from scipy.stats import norm

    if delta <= 0 or delta >= 1:
        return float("nan"), float("nan")
    sigma = atm_iv(fit)
    t = fit.expiry_dte / 365.0
    if not np.isfinite(sigma) or sigma <= 0 or t <= 0:
        return float("nan"), float("nan")
    k_offset = abs(norm.ppf(delta)) * sigma * np.sqrt(t)
    put_var = float(predict_slice(fit, -k_offset)[0])
    call_var = float(predict_slice(fit, k_offset)[0])
    put_iv = float(np.sqrt(max(put_var, 0.0) / t))
    call_iv = float(np.sqrt(max(call_var, 0.0) / t))
    return put_iv, call_iv


def check_calendar_arbitrage(fits: list[SliceFit]) -> list[dict[str, object]]:
    """Return one dict per slice flagging calendar-arb violations.

    A violation occurs when an earlier-expiry slice has *higher*
    total variance at some log-moneyness point than a later-expiry
    slice. We sample at k in {-0.25, 0.0, 0.25} as a cheap diagnostic.
    """
    if len(fits) < 2:
        return []
    by_dte = sorted(fits, key=lambda f: f.expiry_dte)
    violations: list[dict[str, object]] = []
    samples = np.array([-0.25, 0.0, 0.25])
    prev_w = predict_slice(by_dte[0], samples)
    for fit in by_dte[1:]:
        w = predict_slice(fit, samples)
        violation_count = int(np.sum(w < prev_w - 1e-6))
        if violation_count > 0:
            violations.append(
                {"expiry_dte": fit.expiry_dte, "n_violations": violation_count}
            )
        prev_w = np.maximum(prev_w, w)
    return violations


__all__ = [
    "SliceFit",
    "atm_iv",
    "check_calendar_arbitrage",
    "delta_iv",
    "fit_slice",
    "predict_slice",
]
