"""Equity-curve construction and drawdown math.

Given a daily realised-return series, build the (nav, peak, drawdown
from peak) timeseries. Pure-Python helpers — DB IO lives in
``runner.py``.

Conventions:

- ``nav`` is unitless (start at 1.0; today's nav = yesterday's nav
  * (1 + today's realised return)).
- ``drawdown_from_peak`` is ``(nav - peak) / peak``, always <= 0.
- The running peak is the trailing max of nav (monotone
  non-decreasing).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

import numpy as np


@dataclass(slots=True)
class EquityCurvePoint:
    as_of: datetime
    nav: float
    daily_return: float
    cumulative_return: float
    peak_nav: float
    drawdown_from_peak: float


def build_equity_curve(
    daily_returns: Iterable[tuple[datetime, float]],
    *,
    starting_nav: float = 1.0,
) -> list[EquityCurvePoint]:
    """Walk a sequence of ``(as_of, realised_return)`` rows and emit
    the corresponding equity-curve points.

    The input is expected in chronological order; the function does
    not re-sort (callers can do that beforehand).
    """
    out: list[EquityCurvePoint] = []
    nav = float(starting_nav)
    peak = nav
    for as_of, r in daily_returns:
        r = float(r)
        nav = nav * (1.0 + r)
        peak = max(peak, nav)
        cumulative = nav / float(starting_nav) - 1.0
        drawdown = (nav - peak) / peak if peak > 0 else 0.0
        out.append(
            EquityCurvePoint(
                as_of=as_of,
                nav=float(nav),
                daily_return=r,
                cumulative_return=float(cumulative),
                peak_nav=float(peak),
                drawdown_from_peak=float(drawdown),
            )
        )
    return out


def rolling_window_return(
    daily_returns: list[float], *, window: int
) -> float | None:
    """Sum of log-1+r over the trailing ``window`` days, expressed as
    a simple return.

    Used by the level-2 gate which fires on the trailing 5-day P&L.
    Returns ``None`` if there isn't enough history.
    """
    if len(daily_returns) < window:
        return None
    tail = daily_returns[-window:]
    growth = 1.0
    for r in tail:
        growth *= 1.0 + float(r)
    return float(growth - 1.0)


def peak_drawdown(equity_curve: list[EquityCurvePoint]) -> float:
    """Most-negative ``drawdown_from_peak`` over the curve. Returns 0
    on an empty curve."""
    if not equity_curve:
        return 0.0
    return float(min(p.drawdown_from_peak for p in equity_curve))


def daily_return_at(
    equity_curve: list[EquityCurvePoint], as_of: datetime
) -> float | None:
    """Return the daily realised return for the matching as_of, or
    ``None`` if no point matches."""
    for p in equity_curve:
        if p.as_of == as_of:
            return p.daily_return
    return None


def _as_np(values: Iterable[float]) -> np.ndarray:
    return np.asarray(list(values), dtype=float)


__all__ = [
    "EquityCurvePoint",
    "_as_np",
    "build_equity_curve",
    "daily_return_at",
    "peak_drawdown",
    "rolling_window_return",
]
