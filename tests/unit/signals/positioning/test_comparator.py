"""Unit tests for PositioningSignalComparator."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.positioning.comparator import PositioningSignalComparator


class _FixedMethod(SignalMethod):
    def __init__(self, *, method_id: str, outputs: list[SignalOutput]) -> None:
        self.metadata = MethodMetadata(
            method_id=method_id,
            component="positioning_signal",
            name=method_id,
            version="1.0.0",
            description="test double",
            references=[],
        )
        self._outputs = outputs

    def compute(self, data: SignalInput, session) -> list[SignalOutput]:
        return self._outputs


def _output(
    instrument_id: str, value_ts: datetime, raw: float, rank: float, confidence: float = 1.0
) -> SignalOutput:
    return SignalOutput(
        instrument_id=instrument_id,
        value_ts=value_ts,
        observation_ts=value_ts,
        raw_value=raw,
        zscore=0.0,
        rank=rank,
        confidence=confidence,
    )


@pytest.mark.unit
def test_comparator_runs_and_computes_extreme_overlap() -> None:
    """When both methods flag the same instruments as extreme (|raw|>tanh(2)),
    ``extreme_overlap`` is 1.0."""
    extreme = float(np.tanh(2.5))  # > tanh(2)
    mild = 0.2
    ts1 = datetime(2024, 1, 9, tzinfo=UTC)
    ts2 = datetime(2024, 1, 16, tzinfo=UTC)

    a = [
        _output("CL", ts1, extreme, 0.9),
        _output("CL", ts2, extreme, 0.9),
        _output("GC", ts1, mild, 0.5),
    ]
    b = [
        _output("CL", ts1, extreme, 0.9),
        _output("CL", ts2, extreme, 0.9),
        _output("GC", ts1, mild, 0.5),
    ]
    method_a = _FixedMethod(method_id="positioning.cot_zscore.v1", outputs=a)
    method_b = _FixedMethod(method_id="positioning.cot_commercial.v1", outputs=b)

    comparator = PositioningSignalComparator()
    data = SignalInput(
        instrument_ids=["CL", "GC"], as_of=ts2, start=ts1, end=ts2
    )
    result = comparator.compare(method_a, method_b, data, ts1, ts2)

    assert result.metrics["extreme_overlap"] == pytest.approx(1.0)
    assert result.metrics["direction_agreement"] == pytest.approx(1.0)


@pytest.mark.unit
def test_extreme_overlap_zero_when_one_side_silent() -> None:
    """If only A flags extreme but B doesn't, overlap = 0."""
    extreme = float(np.tanh(2.5))
    mild = 0.1
    ts1 = datetime(2024, 1, 9, tzinfo=UTC)
    ts2 = datetime(2024, 1, 16, tzinfo=UTC)

    a = [_output("CL", ts1, extreme, 0.9), _output("CL", ts2, extreme, 0.9)]
    b = [_output("CL", ts1, mild, 0.5), _output("CL", ts2, mild, 0.5)]
    method_a = _FixedMethod(method_id="positioning.cot_zscore.v1", outputs=a)
    method_b = _FixedMethod(method_id="positioning.cot_commercial.v1", outputs=b)

    data = SignalInput(instrument_ids=["CL"], as_of=ts2, start=ts1, end=ts2)
    result = PositioningSignalComparator().compare(method_a, method_b, data, ts1, ts2)

    assert result.metrics["extreme_overlap"] == pytest.approx(0.0)


@pytest.mark.unit
def test_extreme_overlap_zero_when_a_has_no_extremes() -> None:
    """If A never flags extreme, the conditional metric is defined as 0."""
    mild = 0.1
    ts1 = datetime(2024, 1, 9, tzinfo=UTC)
    a = [_output("CL", ts1, mild, 0.5)]
    b = [_output("CL", ts1, mild, 0.5)]
    method_a = _FixedMethod(method_id="positioning.cot_zscore.v1", outputs=a)
    method_b = _FixedMethod(method_id="positioning.cot_commercial.v1", outputs=b)
    data = SignalInput(instrument_ids=["CL"], as_of=ts1, start=ts1, end=ts1)

    result = PositioningSignalComparator().compare(method_a, method_b, data, ts1, ts1)
    assert result.metrics["extreme_overlap"] == pytest.approx(0.0)


@pytest.mark.unit
def test_comparator_skips_empty_outputs() -> None:
    """When neither method emitted anything, the comparator returns empty
    output-count metrics rather than crashing."""
    ts = datetime(2024, 1, 9, tzinfo=UTC)
    method_a = _FixedMethod(method_id="positioning.cot_zscore.v1", outputs=[])
    method_b = _FixedMethod(method_id="positioning.cot_commercial.v1", outputs=[])
    data = SignalInput(instrument_ids=["CL"], as_of=ts, start=ts, end=ts)

    result = PositioningSignalComparator().compare(method_a, method_b, data, ts, ts)
    assert result.metrics["n_observations_a"] == 0.0
    assert result.metrics["n_observations_b"] == 0.0
