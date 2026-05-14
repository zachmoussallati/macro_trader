"""Unit tests for data/loaders.py — return + vol math.

Database-backed loaders are exercised by the integration tests; this file
focuses on pure-Python helpers.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from macro_trader.data.loaders import compute_returns, compute_vol


@pytest.mark.unit
def test_compute_returns_log_default() -> None:
    s = pd.Series([100.0, 101.0, 102.01])
    r = compute_returns(s)
    assert r.iloc[0] != r.iloc[0]  # NaN at the start
    assert r.iloc[1] == pytest.approx(math.log(101.0 / 100.0))
    assert r.iloc[2] == pytest.approx(math.log(102.01 / 101.0))


@pytest.mark.unit
def test_compute_returns_simple() -> None:
    s = pd.Series([100.0, 110.0, 121.0])
    r = compute_returns(s, kind="simple")
    assert r.iloc[1] == pytest.approx(0.10)
    assert r.iloc[2] == pytest.approx(0.10)


@pytest.mark.unit
def test_compute_returns_rejects_invalid_period() -> None:
    with pytest.raises(ValueError):
        compute_returns(pd.Series([1.0, 2.0]), period=0)


@pytest.mark.unit
def test_compute_vol_annualises() -> None:
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.normal(scale=0.01, size=1000))
    vol = compute_vol(rets, window=252)
    # Daily 1% vol annualises to ~16%.
    tail = vol.dropna().iloc[-100:].mean()
    assert 0.10 < tail < 0.25


@pytest.mark.unit
def test_compute_vol_rejects_short_window() -> None:
    with pytest.raises(ValueError):
        compute_vol(pd.Series([0.01, 0.02]), window=1)
