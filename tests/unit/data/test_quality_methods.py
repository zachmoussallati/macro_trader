"""Unit tests for the data-quality methods."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from macro_trader.data.quality.methods import (
    IsolationForestOutlier,
    ZScoreOutlier,
)


# ----------------------------------------------------------------------
# ZScoreOutlier
# ----------------------------------------------------------------------
@pytest.mark.unit
def test_zscore_flags_inserted_outlier() -> None:
    rng = np.random.default_rng(42)
    series = rng.normal(loc=0.0, scale=1.0, size=200)
    series[150] = 100.0  # massive outlier
    m = ZScoreOutlier(threshold=3.0, window=60)
    m.fit(series)
    flags = m.predict(series)
    assert flags[150]
    assert flags.sum() <= 5  # very few false positives on Gaussian noise


@pytest.mark.unit
def test_zscore_returns_correct_shape() -> None:
    m = ZScoreOutlier()
    flags = m.predict(np.zeros(120))
    assert flags.shape == (120,)
    assert flags.dtype == bool


@pytest.mark.unit
def test_zscore_returns_all_false_for_short_input() -> None:
    m = ZScoreOutlier(window=60)
    assert not m.predict(np.zeros(30)).any()


@pytest.mark.unit
def test_zscore_serialize_round_trip() -> None:
    m = ZScoreOutlier(threshold=2.5, window=42)
    restored = ZScoreOutlier.deserialize(m.serialize())
    assert restored.threshold == 2.5
    assert restored.window == 42


@settings(max_examples=50, deadline=None)
@given(st.integers(min_value=70, max_value=400))
def test_zscore_invariant_low_false_positive_rate(n: int) -> None:
    """On uniform-bounded noise the flag rate must stay below an obvious cap."""
    rng = np.random.default_rng(seed=n)
    series = rng.normal(loc=0.0, scale=1.0, size=n)
    flags = ZScoreOutlier(threshold=3.0, window=60).predict(series)
    # |z| > 3 over a 60-day rolling window should fire on << 5% of points
    # for clean Gaussian noise.
    assert flags.mean() <= 0.10


# ----------------------------------------------------------------------
# IsolationForestOutlier
# ----------------------------------------------------------------------
@pytest.mark.unit
def test_isoforest_fits_and_predicts() -> None:
    rng = np.random.default_rng(0)
    series = rng.normal(size=200)
    series[100] = 50.0
    m = IsolationForestOutlier(contamination=0.05, random_state=0)
    m.fit(series)
    flags = m.predict(series)
    assert flags[100]


@pytest.mark.unit
def test_isoforest_serialize_round_trip() -> None:
    rng = np.random.default_rng(1)
    series = rng.normal(size=100)
    m = IsolationForestOutlier(contamination=0.05, random_state=1)
    m.fit(series)
    blob = m.serialize()
    restored = IsolationForestOutlier.deserialize(blob)
    np.testing.assert_array_equal(restored.predict(series), m.predict(series))


@pytest.mark.unit
def test_isoforest_handles_all_nan() -> None:
    m = IsolationForestOutlier()
    m.fit(np.full(50, np.nan))
    assert not m.predict(np.full(50, np.nan)).any()
