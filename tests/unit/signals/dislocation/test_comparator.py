"""DislocationSignalComparator unit tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.dislocation.comparator import DislocationSignalComparator


class _Fixed(SignalMethod):
    def __init__(self, method_id: str, outputs: list[SignalOutput]) -> None:
        self.metadata = MethodMetadata(
            method_id=method_id,
            component="dislocation_signal",
            name=method_id,
            version="1.0.0",
            description="test double",
            references=[],
        )
        self._outputs = outputs

    def compute(self, data, session):
        return self._outputs


def _o(instrument_id: str, ts: datetime, raw: float, rank: float) -> SignalOutput:
    return SignalOutput(
        instrument_id=instrument_id,
        value_ts=ts,
        observation_ts=ts,
        raw_value=raw,
        zscore=0.0,
        rank=rank,
        confidence=0.5,
    )


@pytest.mark.unit
def test_dislocation_comparator_reports_residual_correlation() -> None:
    ts1 = datetime(2024, 1, 5, tzinfo=UTC)
    ts2 = datetime(2024, 1, 12, tzinfo=UTC)
    a = [
        _o("CL", ts1, 0.4, 0.6),
        _o("CL", ts2, 0.5, 0.7),
        _o("GC", ts1, -0.3, 0.3),
        _o("GC", ts2, -0.2, 0.4),
    ]
    # Identical → correlation 1.0.
    b = list(a)
    method_a = _Fixed("dislocation.pca.v1", a)
    method_b = _Fixed("dislocation.dfm.v1", b)
    data = SignalInput(instrument_ids=["CL", "GC"], as_of=ts2, start=ts1, end=ts2)
    result = DislocationSignalComparator().compare(method_a, method_b, data, ts1, ts2)
    assert result.metrics["residual_correlation"] == pytest.approx(1.0)
    assert result.metrics["direction_agreement"] == pytest.approx(1.0)


@pytest.mark.unit
def test_dislocation_comparator_handles_one_side_empty() -> None:
    """If the DFM fit failed (returns []), the comparator must not crash."""
    ts = datetime(2024, 1, 5, tzinfo=UTC)
    method_a = _Fixed("dislocation.pca.v1", [_o("CL", ts, 0.1, 0.5)])
    method_b = _Fixed("dislocation.dfm.v1", [])
    data = SignalInput(instrument_ids=["CL"], as_of=ts, start=ts, end=ts)

    result = DislocationSignalComparator().compare(method_a, method_b, data, ts, ts)
    assert result.metrics["n_observations_a"] >= 1.0
    assert result.metrics["n_observations_b"] == 0.0
