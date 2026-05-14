"""Unit tests for the SignalMethod ABC + SignalOutput schema."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from macro_trader.signals.base import SignalInput, SignalOutput
from macro_trader.signals.output import (
    cross_sectional_rank,
    rolling_zscore_of_self,
)


@pytest.mark.unit
def test_signal_input_carries_as_of_anchor() -> None:
    inp = SignalInput(
        instrument_ids=["CL", "BZ"],
        as_of=datetime(2026, 6, 1, tzinfo=UTC),
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert inp.as_of == datetime(2026, 6, 1, tzinfo=UTC)
    assert inp.calendar == "NYSE"
    assert inp.regime_state is None


@pytest.mark.unit
def test_signal_output_shape() -> None:
    o = SignalOutput(
        instrument_id="CL",
        value_ts=datetime(2026, 6, 1, tzinfo=UTC),
        observation_ts=datetime(2026, 6, 2, tzinfo=UTC),
        raw_value=0.5,
        zscore=1.2,
        rank=0.75,
        confidence=0.95,
        rolling_sharpe_252=1.1,
    )
    assert o.raw_value == 0.5
    assert 0 <= o.rank <= 1
    assert 0 <= o.confidence <= 1


@pytest.mark.unit
def test_cross_sectional_rank_universe_wide() -> None:
    ranks = cross_sectional_rank({"CL": 0.5, "BZ": 0.2, "GC": 0.8})
    assert sorted(ranks.values()) == [
        pytest.approx(1 / 3, abs=0.01),
        pytest.approx(2 / 3, abs=0.01),
        pytest.approx(3 / 3, abs=0.01),
    ]
    # Highest value gets the highest rank.
    assert ranks["GC"] > ranks["CL"] > ranks["BZ"]


@pytest.mark.unit
def test_cross_sectional_rank_sub_class() -> None:
    ranks = cross_sectional_rank(
        {"CL": 0.5, "BZ": 0.2, "GC": 0.8, "SI": 0.1},
        sub_class_groups={
            "energy": ["CL", "BZ"],
            "precious_metals": ["GC", "SI"],
        },
    )
    # Within energy: CL > BZ
    assert ranks["CL"] > ranks["BZ"]
    # Within precious_metals: GC > SI
    assert ranks["GC"] > ranks["SI"]
    # Both maxima get rank=1.0 (within-group pct rank).
    assert ranks["CL"] == pytest.approx(1.0)
    assert ranks["GC"] == pytest.approx(1.0)


@pytest.mark.unit
def test_cross_sectional_rank_single_instrument_centre() -> None:
    ranks = cross_sectional_rank(
        {"ALI": 0.5},
        sub_class_groups={"base_metals": ["ALI"]},
    )
    assert ranks["ALI"] == 0.5


@pytest.mark.unit
def test_rolling_zscore_converges_to_zero_on_stationary() -> None:
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(0)
    series = pd.Series(rng.normal(size=500))
    z = rolling_zscore_of_self(series, lookback=252, min_periods=60)
    tail_mean = float(z.iloc[300:].mean())
    assert abs(tail_mean) < 0.5
