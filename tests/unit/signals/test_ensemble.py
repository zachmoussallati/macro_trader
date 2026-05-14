"""Unit tests for the ensemble combiner."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from macro_trader.signals.base import SignalOutput
from macro_trader.signals.ensemble import equal_weighted


def _output(method_id: str, instrument: str, value_ts: datetime, raw: float) -> SignalOutput:
    return SignalOutput(
        instrument_id=instrument,
        value_ts=value_ts,
        observation_ts=value_ts,
        raw_value=raw,
        zscore=raw,
        rank=0.5,
        confidence=1.0,
        metadata={"source": method_id},
    )


@pytest.mark.unit
def test_equal_weighted_averages_components() -> None:
    ts = datetime(2026, 6, 1, tzinfo=UTC)
    component_outputs = {
        "trend.a": [_output("trend.a", "CL", ts, 0.6)],
        "trend.b": [_output("trend.b", "CL", ts, 0.3)],
        "trend.c": [_output("trend.c", "CL", ts, 0.0)],
    }
    combined = equal_weighted(component_outputs)
    assert len(combined) == 1
    assert combined[0].raw_value == pytest.approx(0.3)
    assert combined[0].metadata["weighting_scheme"] == "equal_weighted"


@pytest.mark.unit
def test_equal_weighted_with_custom_weights_renormalises() -> None:
    ts = datetime(2026, 6, 1, tzinfo=UTC)
    component_outputs = {
        "a": [_output("a", "CL", ts, 1.0)],
        "b": [_output("b", "CL", ts, 0.0)],
    }
    combined = equal_weighted(component_outputs, weights={"a": 3.0, "b": 1.0})
    # Weight 3:1 → 0.75 * 1.0 + 0.25 * 0.0 = 0.75
    assert combined[0].raw_value == pytest.approx(0.75)


@pytest.mark.unit
def test_equal_weighted_regime_state_is_ignored_in_stage_3() -> None:
    ts = datetime(2026, 6, 1, tzinfo=UTC)
    component_outputs = {
        "a": [_output("a", "CL", ts, 0.5)],
        "b": [_output("b", "CL", ts, 0.5)],
    }
    a = equal_weighted(component_outputs, regime_state="risk_off")
    b = equal_weighted(component_outputs, regime_state=None)
    assert a[0].raw_value == b[0].raw_value
