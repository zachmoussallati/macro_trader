"""Unit tests for the regime-conditional weight helpers that don't
touch Postgres (the snapshot-persistence + DB-bound functions are
covered by the integration test in
``tests/integration/composite/test_composite_pipeline.py``)."""

from __future__ import annotations

import math

import pandas as pd

from macro_trader.composite.weights import effective_weights_for_probabilities


def _snapshot(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """Build an indexed DataFrame matching what
    ``load_latest_weight_snapshot`` returns."""
    df = pd.DataFrame(
        rows, columns=["regime_label", "signal_method_id", "weight"]
    )
    df["weight_source"] = "attribution_sharpe"
    df["snapshot_ts"] = pd.Timestamp.utcnow()
    return df.set_index(["regime_label", "signal_method_id"])


def test_empty_snapshot_returns_empty() -> None:
    out = effective_weights_for_probabilities(
        _snapshot([])[:0],
        {"risk_on_growth": 1.0},
    )
    assert out == {}


def test_one_regime_passes_through_weights() -> None:
    snap = _snapshot(
        [
            ("risk_on_growth", "trend.ensemble.v1", 0.6),
            ("risk_on_growth", "carry.spot_proxy.v1", 0.4),
        ]
    )
    out = effective_weights_for_probabilities(
        snap, {"risk_on_growth": 1.0, "vol_spike": 0.0}
    )
    assert math.isclose(out["trend.ensemble.v1"], 0.6)
    assert math.isclose(out["carry.spot_proxy.v1"], 0.4)


def test_two_regimes_blend_by_probabilities() -> None:
    snap = _snapshot(
        [
            ("risk_on_growth", "trend.ensemble.v1", 0.8),
            ("risk_on_growth", "carry.spot_proxy.v1", 0.2),
            ("vol_spike", "trend.ensemble.v1", 0.1),
            ("vol_spike", "carry.spot_proxy.v1", 0.9),
        ]
    )
    out = effective_weights_for_probabilities(
        snap, {"risk_on_growth": 0.7, "vol_spike": 0.3}
    )
    # trend: 0.7*0.8 + 0.3*0.1 = 0.59
    # carry: 0.7*0.2 + 0.3*0.9 = 0.41
    assert math.isclose(out["trend.ensemble.v1"], 0.59, abs_tol=1e-9)
    assert math.isclose(out["carry.spot_proxy.v1"], 0.41, abs_tol=1e-9)
    # And the effective weights should still sum to 1 (since the
    # per-regime weights sum to 1 and probabilities sum to 1).
    assert math.isclose(sum(out.values()), 1.0, abs_tol=1e-9)


def test_zero_probability_drops_regime() -> None:
    snap = _snapshot(
        [
            ("risk_on_growth", "trend.ensemble.v1", 1.0),
            ("vol_spike", "trend.ensemble.v1", 1.0),
        ]
    )
    out = effective_weights_for_probabilities(
        snap, {"risk_on_growth": 1.0, "vol_spike": 0.0}
    )
    assert out == {"trend.ensemble.v1": 1.0}


def test_unknown_regime_in_probability_is_ignored() -> None:
    snap = _snapshot([("risk_on_growth", "trend.ensemble.v1", 1.0)])
    out = effective_weights_for_probabilities(
        snap, {"risk_on_growth": 0.6, "future_regime_v2": 0.4}
    )
    # Only the regime present in snapshot contributes; total is 0.6,
    # not normalised — the caller is responsible for sourcing a valid
    # probability vector.
    assert math.isclose(out["trend.ensemble.v1"], 0.6, abs_tol=1e-9)
