"""Macro factor construction.

Six factors derived from FRED series. Each factor goes through a
transform (yoy_change / 60d_return / level / level_inverted /
60d_change_inverted) and then a rolling z-score over the configured
window. Output: panel of factor z-scores indexed by date.

If a FRED series isn't ingested yet (DFII2 and VIXCLS landed in Stage
4B alongside this module), the factor is silently dropped. Downstream
methods see a smaller factor set and emit lower-dimensional signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd

from macro_trader.data.loaders import load_macro_series
from macro_trader.logging_setup import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


Transform = Literal[
    "yoy_change",
    "60d_return",
    "60d_change_inverted",
    "level",
    "level_inverted",
]


@dataclass(slots=True, frozen=True)
class FactorSpec:
    name: str
    fred_series: str
    transform: Transform
    zscore_window: int = 252


# Default factor set (configurable via signals.factor_exposure.factors
# in config/base.yaml; this module is the source of truth for what the
# names mean).
DEFAULT_FACTORS: tuple[FactorSpec, ...] = (
    FactorSpec(name="growth", fred_series="FRED:INDPRO", transform="yoy_change"),
    FactorSpec(name="inflation", fred_series="FRED:CPIAUCSL", transform="yoy_change"),
    FactorSpec(name="liquidity", fred_series="FRED:DFII2", transform="level_inverted"),
    FactorSpec(name="usd", fred_series="FRED:DTWEXBGS", transform="60d_return"),
    FactorSpec(name="oil", fred_series="FRED:DCOILWTICO", transform="60d_return"),
    FactorSpec(name="risk_on", fred_series="FRED:VIXCLS", transform="60d_change_inverted"),
)


def _apply_transform(series: pd.Series, transform: Transform) -> pd.Series:
    s = series.dropna()
    if s.empty:
        return s
    if transform == "yoy_change":
        return s.pct_change(252).dropna()
    if transform == "60d_return":
        return (s / s.shift(60) - 1.0).dropna()
    if transform == "60d_change_inverted":
        return (-(s - s.shift(60))).dropna()
    if transform == "level":
        return s
    if transform == "level_inverted":
        return -s
    raise ValueError(f"unknown transform: {transform!r}")


def _rolling_zscore(series: pd.Series, window: int, min_periods: int = 60) -> pd.Series:
    if series.empty:
        return series
    mu = series.rolling(window=window, min_periods=min_periods).mean()
    sigma = (
        series.rolling(window=window, min_periods=min_periods)
        .std(ddof=1)
        .replace(0, np.nan)
    )
    return (series - mu) / sigma


def build_factor_panel(
    session: Session,
    *,
    as_of: datetime,
    lookback_days: int = 504,
    factors: tuple[FactorSpec, ...] = DEFAULT_FACTORS,
) -> pd.DataFrame:
    """Return a (date x factor-name) DataFrame of factor z-scores.

    Each factor column is reindexed to a daily UTC date range covering
    [as_of - lookback_days, as_of] and forward-filled across missing
    days (FRED monthly series like INDPRO / CPIAUCSL get the same value
    repeated until the next release).
    """
    start = as_of - timedelta(days=lookback_days + 365)  # extra year for yoy windows
    end = as_of
    columns: dict[str, pd.Series] = {}
    for spec in factors:
        series = load_macro_series(
            session, spec.fred_series, start=start, end=end, as_of=as_of
        )
        if series.empty:
            log.info("signals.factor_exposure.missing_series", series=spec.fred_series)
            continue
        transformed = _apply_transform(series, spec.transform)
        zscored = _rolling_zscore(transformed, window=spec.zscore_window)
        columns[spec.name] = zscored

    if not columns:
        return pd.DataFrame()

    panel = pd.DataFrame(columns)
    panel.index = pd.to_datetime(panel.index, utc=True)
    panel = panel.sort_index()
    # Daily reindex + forward-fill so monthly factors carry between releases.
    daily_idx = pd.date_range(
        start=as_of - timedelta(days=lookback_days), end=as_of, freq="B", tz="UTC"
    )
    panel = panel.reindex(panel.index.union(daily_idx)).sort_index().ffill()
    panel = panel.reindex(daily_idx)
    return panel


def latest_factor_zscores(
    session: Session,
    *,
    as_of: datetime,
    factors: tuple[FactorSpec, ...] = DEFAULT_FACTORS,
) -> dict[str, float]:
    """Convenience: return the most-recent finite z-score per factor."""
    panel = build_factor_panel(session, as_of=as_of, factors=factors)
    if panel.empty:
        return {}
    out: dict[str, float] = {}
    for col in panel.columns:
        s = panel[col].dropna()
        if s.empty:
            continue
        v = float(s.iloc[-1])
        if np.isnan(v) or np.isinf(v):
            continue
        out[col] = v
    return out


__all__ = [
    "DEFAULT_FACTORS",
    "FactorSpec",
    "Transform",
    "build_factor_panel",
    "latest_factor_zscores",
]
