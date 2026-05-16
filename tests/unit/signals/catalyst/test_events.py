"""Catalyst events helpers (pure-Python, no DB)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from macro_trader.signals.catalyst.events import (
    EventSensitivity,
    HistoricalReturn,
    _baseline_vol,
    _log_return_in_window,
    estimate_sensitivities,
    time_decay_weight,
)


def _series(start: str = "2024-01-01", n: int = 100) -> pd.Series:
    idx = pd.date_range(start, periods=n, freq="B", tz="UTC")
    return pd.Series([100.0 + i * 0.5 for i in range(n)], index=idx)


@pytest.mark.unit
def test_log_return_in_window_basic() -> None:
    s = _series()
    event = s.index[10].to_pydatetime().replace(tzinfo=None)
    r = _log_return_in_window(s, event, window_days=(-1, 1))
    assert r is not None
    assert r > 0  # series is strictly increasing


@pytest.mark.unit
def test_log_return_in_window_returns_none_when_too_few_points() -> None:
    s = _series(n=5)
    # Event far outside the series
    r = _log_return_in_window(s, datetime(2030, 1, 1), window_days=(-1, 1))
    assert r is None


@pytest.mark.unit
def test_baseline_vol_finite_for_real_series() -> None:
    s = _series(n=200)
    vol = _baseline_vol(s, window=60)
    assert vol >= 0


@pytest.mark.unit
def test_baseline_vol_zero_for_empty_series() -> None:
    assert _baseline_vol(pd.Series(dtype=float)) == 0.0


@pytest.mark.unit
def test_estimate_sensitivities_drops_low_event_pairs() -> None:
    """Subjects with fewer than min_events historical instances are
    silently dropped; those with enough produce a sensitivity row."""
    historicals = [
        HistoricalReturn(
            instrument_id="CL",
            subject="US CPI",
            event_ts=datetime(2024, 1, i + 1),
            log_return=0.01 if i % 2 == 0 else -0.01,
        )
        for i in range(6)
    ]
    historicals += [
        HistoricalReturn(
            instrument_id="CL",
            subject="Rare event",
            event_ts=datetime(2024, 6, 1),
            log_return=0.05,
        )
    ]
    series = {"CL": _series(n=200)}
    out = estimate_sensitivities(historicals, series_by_inst=series, min_events=5)
    subjects = {s.subject for s in out}
    assert "US CPI" in subjects
    assert "Rare event" not in subjects


@pytest.mark.unit
def test_estimate_sensitivities_value_shape() -> None:
    historicals = [
        HistoricalReturn(
            instrument_id="GC",
            subject="FOMC Decision",
            event_ts=datetime(2024, 1, i + 1),
            log_return=(i - 2) * 0.005,
        )
        for i in range(5)
    ]
    out = estimate_sensitivities(historicals, series_by_inst={"GC": _series(n=120)}, min_events=5)
    assert len(out) == 1
    s = out[0]
    assert isinstance(s, EventSensitivity)
    assert s.n_events == 5
    assert s.mean_abs_return > 0
    assert s.sensitivity == s.mean_abs_return - s.baseline_vol


@pytest.mark.unit
def test_time_decay_linear_weight_decreases_with_distance() -> None:
    assert time_decay_weight(0.0, forward_window_days=10, decay="linear") == 1.0
    assert time_decay_weight(5.0, forward_window_days=10, decay="linear") == 0.5
    assert time_decay_weight(10.0, forward_window_days=10, decay="linear") == 0.0


@pytest.mark.unit
def test_time_decay_returns_zero_outside_window() -> None:
    assert time_decay_weight(-1.0, forward_window_days=10) == 0.0
    assert time_decay_weight(20.0, forward_window_days=10) == 0.0


@pytest.mark.unit
def test_time_decay_exponential_halves_at_half_life() -> None:
    w = time_decay_weight(5.0, forward_window_days=10, decay="exponential")
    assert w == pytest.approx(0.5)


@pytest.mark.unit
def test_time_decay_unknown_strategy_raises() -> None:
    with pytest.raises(ValueError):
        time_decay_weight(5.0, decay="not_a_real_decay")  # type: ignore[arg-type]


@pytest.mark.unit
def test_event_sensitivity_dataclass_round_trip() -> None:
    """The refit module pickles ``[s.__dict__ for s in sensitivities]``
    and reconstructs via ``EventSensitivity(**d)``; verify the round
    trip works on the dataclass fields."""
    from dataclasses import asdict

    s = EventSensitivity(
        instrument_id="CL",
        subject="EIA Weekly",
        n_events=10,
        mean_abs_return=0.012,
        baseline_vol=0.008,
        sensitivity=0.004,
    )
    d = asdict(s)
    s2 = EventSensitivity(**d)
    assert s == s2
