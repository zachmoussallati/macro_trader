"""Random Forest factor exposure tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from macro_trader.signals.factor_exposure.methods import (
    RandomForestFactorExposure,
)


def _synthetic_panel(
    *, n_days: int = 400, n_instruments: int = 3, n_factors: int = 3, seed: int = 0
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    factors = rng.normal(scale=1.0, size=(n_days, n_factors))
    # Non-linear interaction: instrument 0 reacts to F0 * F1
    returns = np.zeros((n_days, n_instruments))
    returns[:, 0] = factors[:, 0] * factors[:, 1] * 0.01 + rng.normal(scale=0.001, size=n_days)
    for i in range(1, n_instruments):
        returns[:, i] = factors @ rng.normal(scale=0.3, size=n_factors) * 0.01 + rng.normal(
            scale=0.001, size=n_days
        )
    idx = pd.date_range("2024-01-01", periods=n_days, freq="B", tz="UTC")
    return (
        pd.DataFrame(
            returns, index=idx, columns=[f"I{i}" for i in range(n_instruments)]
        ),
        pd.DataFrame(
            factors, index=idx, columns=[f"F{j}" for j in range(n_factors)]
        ),
    )


@pytest.mark.unit
def test_rf_metadata() -> None:
    m = RandomForestFactorExposure()
    assert m.metadata.method_id == "factor_exposure.rf.v1"
    assert m.metadata.component == "factor_exposure_signal"


@pytest.mark.unit
def test_rf_fit_produces_state_with_oob_score() -> None:
    returns, factors = _synthetic_panel(n_days=400, n_instruments=2)
    method = RandomForestFactorExposure(
        lookback_days=400, min_history_days=200, n_estimators=50, max_depth=3
    )
    method.fit_on_panels(returns, factors)
    assert method._state is not None
    for _inst, fit in method._state["per_instrument"].items():
        assert "oob_r2" in fit
        assert "feature_importances" in fit
        assert -1.0 <= fit["oob_r2"] <= 1.0


@pytest.mark.unit
def test_rf_predict_at_returns_per_instrument_signal() -> None:
    returns, factors = _synthetic_panel(n_days=300, n_instruments=2)
    method = RandomForestFactorExposure(
        lookback_days=300, min_history_days=200, n_estimators=30
    )
    method.fit_on_panels(returns, factors)
    z = dict.fromkeys(factors.columns, 0.0)
    signals = method.predict_at(z)
    assert set(signals.keys()) == set(returns.columns)
    for v in signals.values():
        assert np.isfinite(v)


@pytest.mark.unit
def test_rf_serialize_round_trip() -> None:
    returns, factors = _synthetic_panel(n_days=300, n_instruments=2)
    method = RandomForestFactorExposure(
        lookback_days=300, min_history_days=200, n_estimators=20
    )
    method.fit_on_panels(returns, factors)
    blob = method.serialize()
    assert blob

    restored = RandomForestFactorExposure.deserialize(blob)
    z = dict.fromkeys(factors.columns, 0.5)
    assert method.predict_at(z) == restored.predict_at(z)


@pytest.mark.unit
def test_rf_unfit_serializes_empty() -> None:
    assert RandomForestFactorExposure().serialize() == b""
    assert RandomForestFactorExposure.deserialize(b"")._state is None
