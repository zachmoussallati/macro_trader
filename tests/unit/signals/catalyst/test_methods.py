"""Tests for the catalyst sensitivity methods (metadata + serialize)."""

from __future__ import annotations

import pickle

import pytest

from macro_trader.signals.catalyst.methods import (
    EventStudyCatalyst,
    _econml_available,
)


@pytest.mark.unit
def test_event_study_metadata() -> None:
    m = EventStudyCatalyst()
    assert m.metadata.method_id == "catalyst.event_study.v1"
    assert m.metadata.component == "catalyst_signal"


@pytest.mark.unit
def test_event_study_unfit_serializes_empty() -> None:
    m = EventStudyCatalyst()
    assert m.serialize() == b""
    assert EventStudyCatalyst.deserialize(b"")._state is None


@pytest.mark.unit
def test_event_study_state_serialise_round_trip() -> None:
    """Manually populate _state with a tiny synthetic payload and check
    that pickle round-trips it."""
    m = EventStudyCatalyst()
    m._state = {
        "sensitivities": [
            {
                "instrument_id": "CL",
                "subject": "EIA Weekly",
                "n_events": 10,
                "mean_abs_return": 0.012,
                "baseline_vol": 0.008,
                "sensitivity": 0.004,
            }
        ],
        "fit_as_of": "2024-12-30T00:00:00",
        "n_historicals": 50,
        "instruments": ["CL", "BZ"],
    }
    blob = m.serialize()
    assert blob

    restored = EventStudyCatalyst.deserialize(blob)
    assert restored._state == m._state
    # Pickle protocol sanity.
    assert pickle.loads(blob)["n_historicals"] == 50


@pytest.mark.unit
def test_event_study_requires_session() -> None:
    from datetime import UTC, datetime

    from macro_trader.signals.base import SignalInput

    inp = SignalInput(
        instrument_ids=["CL"],
        as_of=datetime(2024, 12, 1, tzinfo=UTC),
        start=datetime(2024, 12, 1, tzinfo=UTC),
        end=datetime(2024, 12, 11, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="session"):
        EventStudyCatalyst().compute(inp, None)


@pytest.mark.unit
def test_causal_constructor_raises_when_econml_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without EconML, instantiating CausalCatalyst must raise a clear
    error so the failure is loud."""
    from macro_trader.signals.catalyst import methods

    monkeypatch.setattr(methods, "_econml_available", lambda: False)
    with pytest.raises(RuntimeError, match=r"\[ml\]"):
        methods.CausalCatalyst()


@pytest.mark.skipif(
    not _econml_available(), reason="EconML not installed; run `uv sync --extra ml`"
)
@pytest.mark.unit
def test_causal_metadata() -> None:
    from macro_trader.signals.catalyst.methods import CausalCatalyst

    m = CausalCatalyst()
    assert m.metadata.method_id == "catalyst.causal.v1"
