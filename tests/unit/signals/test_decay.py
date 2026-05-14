"""Unit tests for the rolling-Sharpe decay calculator."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from macro_trader.signals.decay import rolling_sharpe


@pytest.mark.unit
def test_rolling_sharpe_positive_for_correctly_signed_signal() -> None:
    rng = np.random.default_rng(0)
    n = 600
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    # Returns with a clear sign-able pattern: positive when signal is positive.
    signal = pd.Series(np.sin(np.linspace(0, 20, n)), index=idx)
    returns = np.sign(signal).shift(1).fillna(0) * 0.005 + rng.normal(scale=0.005, size=n)
    returns = pd.Series(returns, index=idx)
    sharpe = rolling_sharpe(signal, returns, window=252, vol_target=0.10, min_periods=60)
    tail = sharpe.dropna().iloc[-60:]
    # We baked a 1-sigma signal in; the rolling Sharpe should be clearly positive.
    assert float(tail.mean()) > 0.5


@pytest.mark.unit
def test_rolling_sharpe_handles_zero_signal() -> None:
    idx = pd.date_range("2024-01-01", periods=300, freq="B")
    sharpe = rolling_sharpe(
        pd.Series(np.zeros(300), index=idx),
        pd.Series(np.zeros(300), index=idx),
        window=252,
    )
    # With identically-zero inputs the result is NaN — std is 0 → divide-by-NaN.
    assert sharpe.dropna().empty
