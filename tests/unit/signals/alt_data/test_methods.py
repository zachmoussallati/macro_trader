"""Alt-data signal methods: metadata + session-required + uncovered
instruments emit zero-confidence rows."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from macro_trader.signals.alt_data.methods import (
    EIAStorageSurprise,
    GoogleTrendsSentiment,
    USDAWASDESurprise,
)
from macro_trader.signals.base import SignalInput


@pytest.mark.unit
def test_eia_metadata() -> None:
    m = EIAStorageSurprise()
    assert m.metadata.method_id == "alt_data.eia_storage.v1"
    assert m.metadata.component == "alt_data_signal"


@pytest.mark.unit
def test_usda_metadata() -> None:
    m = USDAWASDESurprise()
    assert m.metadata.method_id == "alt_data.usda_wasde.v1"
    assert m.metadata.component == "alt_data_signal"


@pytest.mark.unit
def test_google_trends_metadata() -> None:
    m = GoogleTrendsSentiment()
    assert m.metadata.method_id == "alt_data.google_trends.v1"
    assert m.metadata.component == "alt_data_signal"


@pytest.mark.unit
def test_alt_data_methods_require_session() -> None:
    inp = SignalInput(
        instrument_ids=["CL"],
        as_of=datetime(2024, 12, 1, tzinfo=UTC),
        start=datetime(2024, 11, 1, tzinfo=UTC),
        end=datetime(2024, 12, 1, tzinfo=UTC),
    )
    for m in (EIAStorageSurprise(), USDAWASDESurprise(), GoogleTrendsSentiment()):
        with pytest.raises(ValueError, match="session"):
            m.compute(inp, None)


@pytest.mark.unit
def test_alt_data_serialize_is_empty() -> None:
    """Alt-data signals are stateless transforms - no _state, no
    serialized blob."""
    for m in (EIAStorageSurprise(), USDAWASDESurprise(), GoogleTrendsSentiment()):
        assert m.serialize() == b""
