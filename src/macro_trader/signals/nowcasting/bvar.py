# Statistical convention uses uppercase X / V_0 / V_n for design /
# prior covariance / posterior covariance matrices (both as
# arguments and locals). Suppress N803 + N806 file-wide.
# ruff: noqa: N803, N806

"""Bayesian linear regression with Minnesota-flavoured prior.

The Stage 5 prompt asks for "BVAR with Minnesota prior using
analytical posterior". Per-release nowcasting is naturally a
univariate regression of the release value on lagged release +
lead indicators, so we implement the *univariate* analogue of
the Minnesota prior:

- Own-lag coefficient shrunk toward 1 (random walk prior).
- Lead-indicator coefficients shrunk toward 0.
- Intercept unshrunk.
- Residual variance follows an Inverse-Gamma prior loosely
  centred on the univariate AR(1) residual variance.

The model is a Normal-Inverse-Gamma (conjugate) Bayesian linear
regression. Posterior parameters are closed-form:

    y = X beta + eps,    eps ~ Normal(0, sigma^2)

with prior

    beta ~ Normal(beta_0, sigma^2 * V_0)
    sigma^2 ~ InvGamma(a_0, b_0)

The posterior is also Normal-Inverse-Gamma with:

    V_n = (V_0^{-1} + X' X)^{-1}
    beta_n = V_n (V_0^{-1} beta_0 + X' y)
    a_n = a_0 + n/2
    b_n = b_0 + 0.5 * (y' y + beta_0' V_0^{-1} beta_0 - beta_n' V_n^{-1} beta_n)

The "Minnesota" piece is in how V_0 is set: tight on own-lag
(small variance, centred at 1), looser on lead indicators
(centred at 0), unrestricted on intercept.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(slots=True)
class BayesianRegressionResult:
    """Posterior summary of a Normal-Inverse-Gamma Bayesian regression."""

    beta_mean: np.ndarray             # (p,)
    beta_cov: np.ndarray              # (p, p) — posterior covariance of beta
    sigma2_posterior_a: float         # InvGamma shape after update
    sigma2_posterior_b: float         # InvGamma scale after update
    n_obs: int

    @property
    def sigma2_mean(self) -> float:
        """E[sigma^2] = b / (a - 1) for a > 1."""
        if self.sigma2_posterior_a <= 1:
            return float("nan")
        return float(self.sigma2_posterior_b / (self.sigma2_posterior_a - 1))

    def predict(self, x_new: np.ndarray) -> tuple[float, float]:
        """Posterior predictive at ``x_new`` (length p).

        Returns ``(mean, predictive_variance)`` where the variance
        accounts for both posterior uncertainty in beta and the
        observation noise sigma^2.
        """
        x = np.asarray(x_new, dtype=float).reshape(-1)
        mean = float(x @ self.beta_mean)
        # Posterior predictive variance under Normal-Inverse-Gamma:
        #   Var = E[sigma^2] * (1 + x' V_n x)
        s2 = self.sigma2_mean
        if not np.isfinite(s2):
            return mean, float("nan")
        bilinear = float(x @ self.beta_cov @ x)
        return mean, s2 * (1.0 + bilinear)


def minnesota_prior_v0(
    n_features: int,
    *,
    own_lag_index: int | None,
    intercept_index: int = 0,
    own_lag_tightness: float = 0.05,
    indicator_tightness: float = 0.5,
    intercept_tightness: float = 100.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Construct ``(beta_0, V_0)`` for the Minnesota-flavoured prior.

    Defaults: tight around (intercept=0, own_lag=1, indicators=0),
    with intercept essentially unrestricted (large variance).
    """
    beta_0 = np.zeros(n_features)
    diag = np.full(n_features, indicator_tightness)
    if intercept_index is not None and 0 <= intercept_index < n_features:
        diag[intercept_index] = intercept_tightness
        beta_0[intercept_index] = 0.0
    if own_lag_index is not None and 0 <= own_lag_index < n_features:
        diag[own_lag_index] = own_lag_tightness
        beta_0[own_lag_index] = 1.0
    V_0 = np.diag(diag)
    return beta_0, V_0


def fit_bayesian_regression(
    X: np.ndarray,
    y: np.ndarray,
    *,
    beta_0: np.ndarray,
    V_0: np.ndarray,
    a_0: float = 2.0,
    b_0: float = 1.0,
) -> BayesianRegressionResult:
    """Conjugate Normal-Inverse-Gamma posterior for univariate OLS.

    Inputs:
        X: (n, p) design matrix (include intercept column externally).
        y: (n,) response.
        beta_0, V_0: prior mean and covariance scaling matrix.
        a_0, b_0: InvGamma shape + scale for the noise variance prior.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1)
    n = X.shape[0]
    if X.ndim != 2 or n != y.shape[0]:
        raise ValueError(f"shape mismatch: X={X.shape} y={y.shape}")

    V0_inv = np.linalg.pinv(V_0)
    XtX = X.T @ X
    Xty = X.T @ y

    V_n = np.linalg.pinv(V0_inv + XtX)
    beta_n = V_n @ (V0_inv @ beta_0 + Xty)

    yty = float(y @ y)
    quad_prior = float(beta_0 @ V0_inv @ beta_0)
    quad_post = float(beta_n @ np.linalg.pinv(V_n) @ beta_n)

    a_n = a_0 + n / 2.0
    b_n = b_0 + 0.5 * (yty + quad_prior - quad_post)
    b_n = max(b_n, 1e-12)  # numerical safety

    return BayesianRegressionResult(
        beta_mean=beta_n,
        beta_cov=V_n,
        sigma2_posterior_a=a_n,
        sigma2_posterior_b=b_n,
        n_obs=n,
    )


__all__ = [
    "BayesianRegressionResult",
    "fit_bayesian_regression",
    "minnesota_prior_v0",
]
