"""Unit tests for the trend-ensemble's per-regime SMA-horizon weights.

Stage 7 wires the ``regime_state`` parameter the ensemble had been
ignoring since Stage 3. These tests verify the pure-Python weight
derivation against the canonical ``signals.trend.regime_adjustments``
config block — no DB / runner involvement.
"""

from __future__ import annotations

import math

import pytest

from macro_trader.signals.trend.methods import _ensemble_weights_for_regime

SMA_METHOD_IDS = [
    "trend.sma_short.v1",
    "trend.sma_medium.v1",
    "trend.sma_long.v1",
]


def test_none_regime_returns_none() -> None:
    """When the regime classifier hasn't run yet, the ensemble falls
    back to Stage 3 equal weights (None signals 'no override')."""
    assert _ensemble_weights_for_regime(SMA_METHOD_IDS, None) is None


def test_unknown_regime_returns_none() -> None:
    assert _ensemble_weights_for_regime(SMA_METHOD_IDS, "made_up_regime") is None


def test_risk_on_growth_equal_weights() -> None:
    """risk_on_growth has all 1.0 multipliers in config -> equal weights."""
    w = _ensemble_weights_for_regime(SMA_METHOD_IDS, "risk_on_growth")
    assert w is not None
    # Base ensemble weights from config are 0.333/0.333/0.334; all
    # multipliers are 1.0 so we recover them after re-normalisation.
    assert math.isclose(w["trend.sma_short.v1"], 0.333, abs_tol=1e-3)
    assert math.isclose(w["trend.sma_medium.v1"], 0.333, abs_tol=1e-3)
    assert math.isclose(w["trend.sma_long.v1"], 0.334, abs_tol=1e-3)


def test_vol_spike_favours_short_horizon() -> None:
    w = _ensemble_weights_for_regime(SMA_METHOD_IDS, "vol_spike")
    assert w is not None
    assert w["trend.sma_short.v1"] > w["trend.sma_medium.v1"]
    assert w["trend.sma_medium.v1"] > w["trend.sma_long.v1"]
    assert math.isclose(sum(w.values()), 1.0, abs_tol=1e-9)


def test_carry_friendly_favours_long_horizon() -> None:
    w = _ensemble_weights_for_regime(SMA_METHOD_IDS, "carry_friendly")
    assert w is not None
    assert w["trend.sma_long.v1"] > w["trend.sma_medium.v1"]
    assert w["trend.sma_medium.v1"] > w["trend.sma_short.v1"]
    assert math.isclose(sum(w.values()), 1.0, abs_tol=1e-9)


def test_risk_off_defensive_favours_short_horizon() -> None:
    w = _ensemble_weights_for_regime(SMA_METHOD_IDS, "risk_off_defensive")
    assert w is not None
    assert w["trend.sma_short.v1"] > w["trend.sma_medium.v1"]
    assert w["trend.sma_medium.v1"] > w["trend.sma_long.v1"]


@pytest.mark.parametrize(
    "regime",
    ["risk_on_growth", "risk_off_defensive", "stagflation", "carry_friendly", "vol_spike"],
)
def test_all_named_regimes_produce_normalised_weights(regime: str) -> None:
    w = _ensemble_weights_for_regime(SMA_METHOD_IDS, regime)
    assert w is not None, f"regime {regime!r} returned no weights"
    assert math.isclose(sum(w.values()), 1.0, abs_tol=1e-9)
    # All weights non-negative.
    assert all(v >= 0 for v in w.values())
