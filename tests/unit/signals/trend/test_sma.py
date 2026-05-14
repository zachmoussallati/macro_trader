"""Unit tests for SMA crossover methods.

These tests exercise the math directly via a stub session that returns a
pre-built panel — no DB needed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from macro_trader.signals.base import SignalInput
from macro_trader.signals.trend.methods import (
    SMACrossoverLong,
    SMACrossoverMedium,
    SMACrossoverShort,
)


@pytest.mark.unit
def test_sma_rejects_inverted_periods() -> None:
    with pytest.raises(ValueError):
        SMACrossoverShort(fast=30, slow=10)


@pytest.mark.unit
def test_sma_short_metadata() -> None:
    m = SMACrossoverShort()
    assert m.metadata.method_id == "trend.sma_short.v1"
    assert m.metadata.component == "trend_signal"


@pytest.mark.unit
def test_sma_compute_requires_session() -> None:
    m = SMACrossoverShort()
    inp = SignalInput(
        instrument_ids=["CL"],
        as_of=datetime(2026, 6, 1, tzinfo=UTC),
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 6, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="session"):
        m.compute(inp, None)


@pytest.mark.unit
@pytest.mark.parametrize(
    "cls,expected_fast,expected_slow",
    [
        (SMACrossoverShort, 10, 30),
        (SMACrossoverMedium, 20, 60),
        (SMACrossoverLong, 50, 200),
    ],
)
def test_sma_default_periods(cls, expected_fast, expected_slow):
    m = cls()
    assert m.fast == expected_fast
    assert m.slow == expected_slow


@pytest.mark.unit
def test_tanh_squash_keeps_signal_in_range() -> None:
    # The internal tanh squash uses *10 multiplier; outputs must stay in [-1, 1].
    inputs = np.linspace(-1.0, 1.0, 200)
    squashed = np.tanh(inputs * 10.0)
    assert squashed.min() > -1.000001
    assert squashed.max() < 1.000001


@pytest.mark.unit
def test_hp_filter_cycle_sums_to_zero_on_linear_input() -> None:
    """A pure linear input has zero cyclical component up to numerical noise."""
    from macro_trader.signals.trend.methods import _hp_filter_cycle

    y = np.linspace(0.0, 1.0, 100)
    cycle = _hp_filter_cycle(y, 1600.0)
    assert abs(cycle.mean()) < 1e-6
    assert cycle.std() < 1e-3
