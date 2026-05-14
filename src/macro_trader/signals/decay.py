"""Rolling Sharpe of the implied position series (signal decay tracker).

Stage 3 ships the in-sample version. Stage 9's walk-forward backtester
replaces this with proper out-of-sample Sharpe.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def rolling_sharpe(
    signal: pd.Series,
    returns: pd.Series,
    *,
    window: int = 252,
    vol_target: float = 0.10,
    trading_days: int = 252,
    min_periods: int | None = None,
) -> pd.Series:
    """Rolling Sharpe of position = sign(signal) sized to ``vol_target``.

    ``signal`` and ``returns`` must share an index. The implied daily PnL
    is::

        scale  = vol_target / realised_vol(returns, lookback=window)
        pnl    = scale.shift(1) * sign(signal).shift(1) * returns

    The ``.shift(1)`` is critical for honesty: yesterday's signal sizes
    today's position. Sharpe is computed as
    ``mean / std * sqrt(trading_days)`` on the rolling window.

    Returns NaN where either input is missing or the rolling window has
    too few observations.
    """
    if signal.empty or returns.empty:
        return pd.Series(dtype=float, index=signal.index)

    common = signal.index.intersection(returns.index)
    sig = signal.reindex(common).astype(float)
    rets = returns.reindex(common).astype(float)

    if sig.empty:
        return pd.Series(dtype=float, index=signal.index)

    min_p = min_periods if min_periods is not None else max(20, window // 4)
    realised = rets.rolling(window=window, min_periods=min_p).std(ddof=1)
    realised = realised.replace(0, np.nan) * math.sqrt(trading_days)
    scale = (vol_target / realised).clip(upper=10.0)

    position = np.sign(sig).shift(1) * scale.shift(1)
    pnl = position * rets

    mean = pnl.rolling(window=window, min_periods=min_p).mean()
    std = pnl.rolling(window=window, min_periods=min_p).std(ddof=1).replace(0, np.nan)
    sharpe = (mean / std) * math.sqrt(trading_days)
    return sharpe.reindex(signal.index)
