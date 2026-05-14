"""Unit tests for the carry placeholder."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from macro_trader.signals.base import SignalInput
from macro_trader.signals.carry.methods import PROXY_CAPABLE, CarrySpotProxy


@pytest.mark.unit
def test_carry_metadata_signals_placeholder_status() -> None:
    m = CarrySpotProxy()
    assert m.metadata.method_id == "carry.spot_proxy.v1"
    assert "PLACEHOLDER" in m.metadata.description


@pytest.mark.unit
def test_proxy_capable_subset_is_documented() -> None:
    assert set(PROXY_CAPABLE) == {"CL", "BZ", "GC", "NG"}


@pytest.mark.unit
def test_carry_compute_requires_session() -> None:
    inp = SignalInput(
        instrument_ids=["CL"],
        as_of=datetime(2026, 6, 1, tzinfo=UTC),
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 6, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="session"):
        CarrySpotProxy().compute(inp, None)


@pytest.mark.unit
def test_carry_confidence_low_for_unsupported() -> None:
    m = CarrySpotProxy()
    assert m.zero_confidence == 0.0
    assert m.proxy_confidence > 0.0
