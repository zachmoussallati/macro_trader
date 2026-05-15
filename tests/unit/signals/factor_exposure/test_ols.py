"""OLS factor exposure method tests."""

from __future__ import annotations

import pickle
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from macro_trader.signals.base import SignalInput
from macro_trader.signals.factor_exposure.methods import OLSFactorExposure


def _synthetic_returns_and_factors(
    *, n_days: int = 300, n_instruments: int = 4, n_factors: int = 3, seed: int = 0
) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    betas = rng.normal(scale=0.5, size=(n_instruments, n_factors))
    factors = rng.normal(scale=1.0, size=(n_days, n_factors))
    eps = rng.normal(scale=0.001, size=(n_days, n_instruments))
    returns = factors @ betas.T + eps
    idx = pd.date_range("2024-01-01", periods=n_days, freq="B", tz="UTC")
    returns_df = pd.DataFrame(
        returns, index=idx, columns=[f"I{i}" for i in range(n_instruments)]
    )
    factor_df = pd.DataFrame(
        factors, index=idx, columns=[f"F{j}" for j in range(n_factors)]
    )
    return returns_df, factor_df, betas


@pytest.mark.unit
def test_ols_metadata() -> None:
    m = OLSFactorExposure()
    assert m.metadata.method_id == "factor_exposure.ols.v1"
    assert m.metadata.component == "factor_exposure_signal"


@pytest.mark.unit
def test_ols_fit_recovers_known_betas_within_tolerance() -> None:
    """Fit OLS on synthetic data with known betas; check that each
    fitted beta is within 0.1 of the true value."""
    returns, factors, true_betas = _synthetic_returns_and_factors(
        n_days=400, n_instruments=4, n_factors=3
    )

    method = OLSFactorExposure(lookback_days=400, min_history_days=300)
    method.fit_on_panels(returns, factors)
    assert method._state is not None

    for inst_idx, inst in enumerate(returns.columns):
        fit = method._state["per_instrument"][inst]
        for fac_idx, factor_name in enumerate(factors.columns):
            recovered = fit["betas"][factor_name]
            true_value = true_betas[inst_idx, fac_idx]
            assert abs(recovered - true_value) < 0.1, (
                f"{inst} x {factor_name}: recovered {recovered:.3f} vs true {true_value:.3f}"
            )


@pytest.mark.unit
def test_ols_predict_signal_sign_matches_inverted_beta_dot_z() -> None:
    """For known fitted betas, predict_at(z) should equal -beta @ z."""
    returns, factors, _ = _synthetic_returns_and_factors(n_days=300, n_instruments=2)
    method = OLSFactorExposure(lookback_days=300, min_history_days=200)
    method.fit_on_panels(returns, factors)
    assert method._state is not None

    z = dict.fromkeys(factors.columns, 1.0)
    signals = method.predict_at(z)
    for inst, signal in signals.items():
        betas = method._state["per_instrument"][inst]["betas"]
        expected = -sum(betas.values())
        assert abs(signal - expected) < 1e-9


@pytest.mark.unit
def test_ols_serialize_round_trip_preserves_predictions() -> None:
    returns, factors, _ = _synthetic_returns_and_factors(n_days=300, n_instruments=3)
    method = OLSFactorExposure(lookback_days=300, min_history_days=200)
    method.fit_on_panels(returns, factors)
    blob = method.serialize()
    assert blob

    restored = OLSFactorExposure.deserialize(blob)
    z = dict.fromkeys(factors.columns, 0.5)
    assert method.predict_at(z) == restored.predict_at(z)


@pytest.mark.unit
def test_ols_unfit_serializes_empty() -> None:
    m = OLSFactorExposure()
    assert m.serialize() == b""
    assert OLSFactorExposure.deserialize(b"")._state is None


@pytest.mark.unit
def test_ols_predict_at_returns_empty_when_unfit() -> None:
    assert OLSFactorExposure().predict_at({"F0": 0.0}) == {}


@pytest.mark.unit
def test_ols_requires_session() -> None:
    inp = SignalInput(
        instrument_ids=["I0"],
        as_of=datetime(2024, 12, 1, tzinfo=UTC),
        start=datetime(2024, 11, 1, tzinfo=UTC),
        end=datetime(2024, 12, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="session"):
        OLSFactorExposure().compute(inp, None)


@pytest.mark.unit
def test_ols_state_dict_carries_fit_rows() -> None:
    returns, factors, _ = _synthetic_returns_and_factors(n_days=300, n_instruments=2)
    method = OLSFactorExposure(lookback_days=300, min_history_days=200)
    method.fit_on_panels(returns, factors)
    assert method._state is not None
    assert method._state["fit_rows"] == 300


@pytest.mark.unit
def test_ols_state_size_under_toast_threshold() -> None:
    """Sanity check: serialised OLS state for 5 instruments x 6 factors
    stays well under 8 KB. (RF/CF state is larger and may need
    compression; OLS does not.)"""
    returns, factors, _ = _synthetic_returns_and_factors(
        n_days=300, n_instruments=5, n_factors=6
    )
    method = OLSFactorExposure(lookback_days=300, min_history_days=200)
    method.fit_on_panels(returns, factors)
    blob = method.serialize()
    assert len(blob) < 8 * 1024


@pytest.mark.unit
def test_ols_returns_no_state_when_inner_join_drops_too_many_rows() -> None:
    """Current implementation does inner-join + dropna(how=any) on the
    aligned (returns, factors) panel, so a single instrument with
    mostly-NaN values shrinks the fit window for everyone. State stays
    ``None`` when the resulting window can't satisfy
    ``min_history_days``. Per-instrument skipping is tracked as a
    Stage 4C improvement (notes/stage_4b/tradeoffs.md)."""
    returns, factors, _ = _synthetic_returns_and_factors(n_days=300, n_instruments=3)
    returns.iloc[:280, 0] = np.nan  # leaves only ~20 usable rows after join
    method = OLSFactorExposure(lookback_days=300, min_history_days=200)
    method.fit_on_panels(returns, factors)
    assert method._state is None


@pytest.mark.unit
def test_ols_serialize_uses_pickle_protocol_highest() -> None:
    """Sanity: serialised payload deserialises via pickle (i.e. we're
    actually using pickle, not some custom format that won't survive
    upgrades)."""
    returns, factors, _ = _synthetic_returns_and_factors(n_days=300, n_instruments=2)
    method = OLSFactorExposure(lookback_days=300, min_history_days=200)
    method.fit_on_panels(returns, factors)
    blob = method.serialize()
    state = pickle.loads(blob)
    assert "per_instrument" in state
    assert "factor_columns" in state
