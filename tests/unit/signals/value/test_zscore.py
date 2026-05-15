"""Unit tests for value signal methods."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from macro_trader.signals.base import SignalInput
from macro_trader.signals.value.methods import (
    CrossSectionalValue,
    ZScoreValue,
)


@pytest.mark.unit
def test_zscore_value_metadata() -> None:
    m = ZScoreValue()
    assert m.metadata.method_id == "value.zscore.v1"
    assert m.metadata.component == "value_signal"


@pytest.mark.unit
def test_cross_sectional_value_metadata() -> None:
    m = CrossSectionalValue()
    assert m.metadata.method_id == "value.cross_sectional.v1"
    assert m.metadata.component == "value_signal"
    # Stage 4B: default column is sub_class (after reseed); the previous
    # asset_class default is still available as an override.
    assert m.class_column == "sub_class"


@pytest.mark.unit
def test_cross_sectional_value_accepts_asset_class_column_override() -> None:
    m = CrossSectionalValue(class_column="asset_class")
    assert m.class_column == "asset_class"


@pytest.mark.unit
def test_value_compute_requires_session() -> None:
    inp = SignalInput(
        instrument_ids=["CL"],
        as_of=datetime(2026, 6, 1, tzinfo=UTC),
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 6, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="session"):
        ZScoreValue().compute(inp, None)
    with pytest.raises(ValueError, match="session"):
        CrossSectionalValue().compute(inp, None)
