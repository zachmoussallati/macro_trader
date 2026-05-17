"""Regime feature panel construction.

10 features the regime classifier uses:

1-6. The six macro factor z-scores from
     ``signals.factor_exposure.factors.build_factor_panel``
     (growth / inflation / liquidity / usd / oil / risk_on).
7.   ``realized_vol_60d`` — 60-day std of SPY daily log-returns.
8.   ``credit_spread`` — (HYG close / IEF close - 1) proxy.
9.   ``yield_curve_slope`` — DGS10 - DGS2 from FRED.
10.  ``vix_level`` — VIXCLS from FRED.

Output: DataFrame indexed by business-day UTC dates, columns
named as above. Missing features are silently dropped — downstream
methods see a smaller panel and document the missing entries.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from macro_trader.data.loaders import load_close_series, load_macro_series
from macro_trader.logging_setup import get_logger
from macro_trader.signals.factor_exposure.factors import build_factor_panel

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


REGIME_FEATURE_COLUMNS: tuple[str, ...] = (
    "growth",
    "inflation",
    "liquidity",
    "usd",
    "oil",
    "risk_on",
    "realized_vol_60d",
    "credit_spread",
    "yield_curve_slope",
    "vix_level",
)


def _safe_load_close_series(
    session: Session,
    instrument_id: str,
    *,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> pd.Series:
    """Tolerant ETF close-series loader; returns empty series if
    the instrument isn't seeded yet (common in test fixtures)."""
    try:
        return load_close_series(
            session, instrument_id, start=start, end=end, as_of=as_of
        )
    except Exception as exc:  # pragma: no cover - missing instrument
        log.info(
            "regime.features.missing_close_series",
            instrument=instrument_id,
            error=str(exc),
        )
        return pd.Series(dtype=float)


def build_regime_features(
    session: Session,
    *,
    as_of: datetime,
    lookback_days: int = 504,
) -> pd.DataFrame:
    """Build the 10-feature regime panel.

    Returns a DataFrame indexed by business-day UTC timestamps over
    [as_of - lookback_days, as_of], columns drawn from
    ``REGIME_FEATURE_COLUMNS`` for whichever features have data.
    """
    # Macro factor z-scores (already z-scored over 252-day windows
    # inside build_factor_panel).
    factors = build_factor_panel(session, as_of=as_of, lookback_days=lookback_days)

    daily_index = pd.date_range(
        start=as_of - timedelta(days=lookback_days),
        end=as_of,
        freq="B",
        tz="UTC",
    )
    panel = factors.reindex(daily_index).ffill() if not factors.empty else pd.DataFrame(
        index=daily_index
    )

    # Realized vol from SPY (if SPY instrument seeded).
    start = as_of - timedelta(days=lookback_days + 90)
    spy = _safe_load_close_series(session, "SPY", start=start, end=as_of, as_of=as_of)
    if not spy.empty:
        spy_logret = np.log(spy.replace(0, np.nan)).diff()
        rv = spy_logret.rolling(60, min_periods=20).std() * np.sqrt(252)
        rv = rv.reindex(daily_index).ffill()
        panel["realized_vol_60d"] = rv

    # Credit spread proxy via HYG / IEF ratio.
    hyg = _safe_load_close_series(session, "HYG", start=start, end=as_of, as_of=as_of)
    ief = _safe_load_close_series(session, "IEF", start=start, end=as_of, as_of=as_of)
    if not hyg.empty and not ief.empty:
        ratio = (hyg / ief - 1.0).reindex(daily_index).ffill()
        panel["credit_spread"] = ratio

    # Yield curve slope DGS10 - DGS2 from FRED.
    dgs10 = load_macro_series(
        session, "FRED:DGS10", start=start, end=as_of, as_of=as_of
    )
    dgs2 = load_macro_series(
        session, "FRED:DGS2", start=start, end=as_of, as_of=as_of
    )
    if not dgs10.empty and not dgs2.empty:
        slope = (dgs10 - dgs2).reindex(daily_index).ffill()
        panel["yield_curve_slope"] = slope

    # VIX level.
    vix = load_macro_series(
        session, "FRED:VIXCLS", start=start, end=as_of, as_of=as_of
    )
    if not vix.empty:
        panel["vix_level"] = vix.reindex(daily_index).ffill()

    # Re-order columns to the canonical list, keep only those present.
    cols = [c for c in REGIME_FEATURE_COLUMNS if c in panel.columns]
    return panel[cols]


__all__ = ["REGIME_FEATURE_COLUMNS", "build_regime_features"]
