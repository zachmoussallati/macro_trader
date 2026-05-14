"""Public data-loading API.

Stage 3 graduates the previously-private ``_load_close_series`` helper
from ``data/quality/runner.py`` into a proper module. Every signal in
``macro_trader.signals.*`` consumes data through this module so the
point-in-time + holiday-masking + return-calculation conventions stay
identical across the codebase.

Public surface:

- :func:`load_close_series`  — latest-vintage close per value_ts for one instrument
- :func:`load_close_panel`   — same for a list of instruments, aligned on a calendar
- :func:`load_macro_series`  — vintaged macro series via ALFRED's `as_of` semantics
- :func:`compute_returns`    — simple / log returns with configurable period
- :func:`compute_vol`        — rolling realised volatility, optionally annualised

All loaders take an ``as_of`` parameter that filters market-data rows on
``observation_ts <= as_of`` and macro-data rows on
``realtime_start <= as_of < realtime_end (or NULL)``. Never call
``.order_by(value_ts.desc()).limit(1)`` without an ``as_of`` filter.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd
from sqlalchemy import or_, select

from macro_trader.db.models.macro_data import SeriesObservation
from macro_trader.db.models.market_data import DailyBar
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ----------------------------------------------------------------------
# Calendar helpers
# ----------------------------------------------------------------------
_CAL_CACHE: dict[str, pd.DatetimeIndex] = {}


def _trading_index(*, calendar: str, start: datetime, end: datetime) -> pd.DatetimeIndex:
    """Return a DatetimeIndex of trading days in [start, end] for `calendar`.

    Falls back to plain business days (Mon-Fri) if
    ``pandas_market_calendars`` cannot find the calendar name — keeps
    tests / dev environments without market-calendar data working.
    """
    key = f"{calendar}|{start.date().isoformat()}|{end.date().isoformat()}"
    cached = _CAL_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        import pandas_market_calendars as mcal

        cal = mcal.get_calendar(calendar)
        sched = cal.schedule(start_date=start.date(), end_date=end.date())
        idx = pd.DatetimeIndex(sched.index.tz_localize(None))
    except Exception:
        idx = pd.bdate_range(start=start.date(), end=end.date())

    idx = idx.tz_localize("UTC")
    _CAL_CACHE[key] = idx
    return idx


# ----------------------------------------------------------------------
# Market data
# ----------------------------------------------------------------------
def load_close_series(
    session: Session,
    instrument_id: str,
    *,
    start: datetime,
    end: datetime,
    as_of: datetime | None = None,
    calendar: str = "NYSE",
    ffill_limit: int = 1,
) -> pd.Series:
    """Return the latest-vintage close per ``value_ts`` for one instrument.

    The series index is the trading-day calendar between ``start`` and
    ``end`` (inclusive on both). Missing observations are forward-filled
    up to ``ffill_limit`` days; longer gaps stay NaN.

    ``as_of`` filters on ``observation_ts <= as_of`` so the query returns
    the snapshot that would have been visible at that time. Defaults to
    ``utcnow()``.
    """
    target = as_of if as_of is not None else utcnow()

    stmt = (
        select(DailyBar.value_ts, DailyBar.close, DailyBar.observation_ts)
        .where(DailyBar.instrument_id == instrument_id)
        .where(DailyBar.value_ts >= start)
        .where(DailyBar.value_ts <= end)
        .where(DailyBar.observation_ts <= target)
        .order_by(DailyBar.value_ts, DailyBar.observation_ts.desc())
    )
    rows = list(session.execute(stmt).all())

    # Dedupe by value_ts keeping the first (latest observation_ts).
    by_ts: dict[pd.Timestamp, float] = {}
    for value_ts, close, _obs in rows:
        ts = pd.Timestamp(value_ts)
        if ts in by_ts:
            continue
        by_ts[ts] = float(close) if close is not None else float("nan")

    if not by_ts:
        idx = _trading_index(calendar=calendar, start=start, end=end)
        return pd.Series(index=idx, dtype=float, name=instrument_id)

    raw = pd.Series(by_ts, name=instrument_id).sort_index()
    raw.index = pd.to_datetime(raw.index, utc=True)
    idx = _trading_index(calendar=calendar, start=start, end=end)
    out = raw.reindex(idx)
    if ffill_limit > 0:
        out = out.ffill(limit=ffill_limit)
    return out


def load_close_panel(
    session: Session,
    instrument_ids: list[str],
    *,
    start: datetime,
    end: datetime,
    as_of: datetime | None = None,
    calendar: str = "NYSE",
    ffill_limit: int = 1,
) -> pd.DataFrame:
    """Load close series for many instruments, aligned on the calendar."""
    if not instrument_ids:
        idx = _trading_index(calendar=calendar, start=start, end=end)
        return pd.DataFrame(index=idx)
    columns: dict[str, pd.Series] = {}
    for instrument_id in instrument_ids:
        columns[instrument_id] = load_close_series(
            session,
            instrument_id,
            start=start,
            end=end,
            as_of=as_of,
            calendar=calendar,
            ffill_limit=ffill_limit,
        )
    return pd.DataFrame(columns)


# ----------------------------------------------------------------------
# Macro data
# ----------------------------------------------------------------------
def load_macro_series(
    session: Session,
    series_id: str,
    *,
    start: datetime,
    end: datetime,
    as_of: datetime | None = None,
) -> pd.Series:
    """Return the FRED/ALFRED vintage of ``series_id`` visible at ``as_of``.

    For each ``value_ts`` the function picks the row whose
    ``realtime_start <= as_of`` and (``realtime_end`` is NULL or
    ``>= as_of``). This is the value a model would have seen at ``as_of``.
    """
    target = as_of if as_of is not None else utcnow()
    stmt = (
        select(SeriesObservation.value_ts, SeriesObservation.value)
        .where(SeriesObservation.series_id == series_id)
        .where(SeriesObservation.value_ts >= start)
        .where(SeriesObservation.value_ts <= end)
        .where(
            or_(
                SeriesObservation.realtime_start.is_(None),
                SeriesObservation.realtime_start <= target,
            )
        )
        .where(
            or_(
                SeriesObservation.realtime_end.is_(None),
                SeriesObservation.realtime_end >= target,
            )
        )
        .order_by(SeriesObservation.value_ts)
    )
    rows = list(session.execute(stmt).all())
    if not rows:
        return pd.Series(dtype=float, name=series_id)
    idx = pd.to_datetime([r[0] for r in rows], utc=True)
    values = [float(r[1]) if r[1] is not None else float("nan") for r in rows]
    return pd.Series(values, index=idx, name=series_id)


# ----------------------------------------------------------------------
# Return + vol math
# ----------------------------------------------------------------------
def compute_returns(
    series: pd.Series,
    *,
    kind: Literal["log", "simple"] = "log",
    period: int = 1,
) -> pd.Series:
    """Compute returns. Default is log-return, period 1."""
    if period < 1:
        raise ValueError("period must be >= 1")
    if kind == "log":
        return np.log(series / series.shift(period))
    if kind == "simple":
        return series / series.shift(period) - 1.0
    raise ValueError(f"unknown return kind {kind!r}")


def compute_vol(
    series: pd.Series,
    *,
    window: int = 20,
    annualize: bool = True,
    trading_days: int = 252,
) -> pd.Series:
    """Rolling realised volatility of a return series."""
    if window < 2:
        raise ValueError("window must be >= 2")
    vol = series.rolling(window=window, min_periods=max(2, window // 2)).std(ddof=1)
    if annualize:
        vol = vol * math.sqrt(trading_days)
    return vol
