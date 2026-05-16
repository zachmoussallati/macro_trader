"""Nowcasting BVAR math tests (pure-numpy, no DB)."""

from __future__ import annotations

import numpy as np
import pytest

from macro_trader.signals.nowcasting.bvar import (
    fit_bayesian_regression,
    minnesota_prior_v0,
)


@pytest.mark.unit
def test_minnesota_prior_centres_own_lag_at_one() -> None:
    beta_0, V_0 = minnesota_prior_v0(
        n_features=4, own_lag_index=1, intercept_index=0
    )
    assert beta_0.shape == (4,)
    assert V_0.shape == (4, 4)
    assert beta_0[1] == 1.0  # own-lag prior mean
    assert beta_0[0] == 0.0  # intercept
    # Indicator coefficients (indices 2, 3) prior mean 0.
    assert beta_0[2] == 0.0
    assert beta_0[3] == 0.0
    # Own-lag variance tightest; intercept variance loosest.
    diag = np.diag(V_0)
    assert diag[1] < diag[2]   # own-lag tighter than indicator
    assert diag[0] > diag[2]   # intercept looser than indicator


@pytest.mark.unit
def test_bayesian_regression_recovers_known_betas() -> None:
    """Fit on a known DGP; posterior mean should be close to true
    beta when the prior is sufficiently uninformative."""
    rng = np.random.default_rng(0)
    n = 200
    p = 3
    true_beta = np.array([0.5, 0.9, -0.3])
    X = np.column_stack(
        [
            np.ones(n),                           # intercept
            rng.normal(size=n),                   # "own lag"
            rng.normal(size=n),                   # indicator
        ]
    )
    y = X @ true_beta + rng.normal(scale=0.1, size=n)

    # Loose prior so the data dominates.
    beta_0 = np.zeros(p)
    V_0 = np.diag(np.full(p, 100.0))
    result = fit_bayesian_regression(X, y, beta_0=beta_0, V_0=V_0)

    assert np.allclose(result.beta_mean, true_beta, atol=0.1)
    assert result.sigma2_mean > 0


@pytest.mark.unit
def test_bayesian_regression_minnesota_shrinks_own_lag_toward_one() -> None:
    """With a *very* tight Minnesota prior the own-lag coefficient
    barely budges from the prior mean of 1.0."""
    rng = np.random.default_rng(1)
    n = 50
    # Build a DGP where the OLS own-lag would be ~0.3.
    own_lag_x = rng.normal(size=n)
    X = np.column_stack([np.ones(n), own_lag_x])
    y = 0.0 + 0.3 * own_lag_x + rng.normal(scale=0.2, size=n)

    beta_0, V_0 = minnesota_prior_v0(
        n_features=2,
        own_lag_index=1,
        intercept_index=0,
        own_lag_tightness=0.001,   # extremely tight
        intercept_tightness=100.0,
    )
    result = fit_bayesian_regression(X, y, beta_0=beta_0, V_0=V_0)
    # Own-lag should be much closer to 1.0 (prior) than 0.3 (OLS).
    assert abs(result.beta_mean[1] - 1.0) < abs(result.beta_mean[1] - 0.3)


@pytest.mark.unit
def test_bayesian_regression_predict_returns_finite_mean_and_variance() -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(50, 3))
    X[:, 0] = 1.0
    y = X @ np.array([0.1, 0.5, -0.2]) + rng.normal(scale=0.1, size=50)
    beta_0 = np.zeros(3)
    V_0 = np.diag(np.full(3, 10.0))
    res = fit_bayesian_regression(X, y, beta_0=beta_0, V_0=V_0)
    mean, var = res.predict(np.array([1.0, 0.5, 0.0]))
    assert np.isfinite(mean)
    assert np.isfinite(var) and var > 0


@pytest.mark.unit
def test_bayesian_regression_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        fit_bayesian_regression(
            np.zeros((5, 3)),
            np.zeros(4),
            beta_0=np.zeros(3),
            V_0=np.eye(3),
        )
