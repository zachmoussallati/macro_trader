"""Unit tests for value signal methods."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from macro_trader.signals.base import SignalInput
from macro_trader.signals.value.methods import (
    DEFAULT_SUB_CLASS_GROUPS,
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
    # Spec sub-class groupings.
    assert m.sub_class_groups["agriculture"] == ["ZC", "ZS", "ZW"]


@pytest.mark.unit
def test_cross_sectional_value_uses_default_groups() -> None:
    assert DEFAULT_SUB_CLASS_GROUPS["energy"] == ["CL", "BZ", "NG", "HO", "RB"]
    assert "ALI" in DEFAULT_SUB_CLASS_GROUPS["base_metals"]


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
