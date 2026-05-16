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


@pytest.mark.skipif(
    not _econml_available(), reason="EconML not installed; run `uv sync --extra ml`"
)
@pytest.mark.unit
def test_causal_state_round_trip_via_pickle() -> None:
    """Stage 4C: the CausalCatalyst now persists CATE per pair, not the
    event-study state. Verify the new ``cates`` shape pickles cleanly."""
    from macro_trader.signals.catalyst.methods import CausalCatalyst

    m = CausalCatalyst()
    m._state = {
        "cates": [
            {
                "instrument_id": "CL",
                "subject": "EIA Weekly",
                "cate_at_today": 0.0042,
                "fallback": False,
                "n_events_used": 22,
            }
        ],
        "factor_columns": ["growth", "inflation"],
        "fit_as_of": "2024-12-30T00:00:00",
        "n_historicals": 22,
        "instruments": ["CL"],
    }
    blob = m.serialize()
    assert blob

    restored = CausalCatalyst.deserialize(blob)
    assert restored._state == m._state


@pytest.mark.skipif(
    not _econml_available(), reason="EconML not installed; run `uv sync --extra ml`"
)
@pytest.mark.unit
def test_causal_constructor_does_not_raise_with_econml() -> None:
    """Stage 4C: with EconML installed, the constructor builds an
    instance with no fitted state (no longer the event-study placeholder)."""
    from macro_trader.signals.catalyst.methods import CausalCatalyst

    m = CausalCatalyst()
    assert m._state is None
    # Constructor params surface on the instance for refit code to read.
    assert m.min_events_for_cate == 15
    assert m.n_estimators == 200
    assert m.min_samples_leaf == 10


@pytest.mark.skipif(
    not _econml_available(), reason="EconML not installed; run `uv sync --extra ml`"
)
@pytest.mark.unit
def test_causal_compute_returns_outputs_with_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end smoke: feed CausalCatalyst a stubbed _state and a
    stubbed upcoming_score; verify it produces SignalOutput rows
    tagged with the new metadata (no `placeholder_for_cate` anymore)."""
    from datetime import UTC, datetime

    from macro_trader.signals.base import SignalInput
    from macro_trader.signals.catalyst import methods as catalyst_methods

    def _fake_upcoming_score(
        session, *, instrument_ids, as_of, sensitivities, **_kwargs
    ):
        scores = dict.fromkeys(instrument_ids, 0.0)
        for s in sensitivities:
            scores[s.instrument_id] = scores.get(s.instrument_id, 0.0) + s.sensitivity
        return scores

    monkeypatch.setattr(catalyst_methods, "upcoming_score", _fake_upcoming_score)

    m = catalyst_methods.CausalCatalyst()
    m._state = {
        "cates": [
            {
                "instrument_id": "CL",
                "subject": "EIA Weekly",
                "cate_at_today": 0.05,
                "fallback": False,
                "n_events_used": 30,
            }
        ],
        "factor_columns": ["growth"],
        "fit_as_of": "2024-12-30T00:00:00",
        "n_historicals": 30,
        "instruments": ["CL"],
    }
    inp = SignalInput(
        instrument_ids=["CL"],
        as_of=datetime(2024, 12, 30, tzinfo=UTC),
        start=datetime(2024, 12, 30, tzinfo=UTC),
        end=datetime(2025, 1, 9, tzinfo=UTC),
    )
    outputs = m.compute(inp, session=object())
    assert outputs
    o = outputs[0]
    assert o.instrument_id == "CL"
    assert isinstance(o.metadata, dict)
    assert o.metadata["method_id"] == "catalyst.causal.v1"
    # Stage 4B's placeholder flag is gone in Stage 4C.
    assert "placeholder_for_cate" not in o.metadata
    assert "fallback_pairs" in o.metadata
