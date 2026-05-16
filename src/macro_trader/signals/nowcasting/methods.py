"""Nowcasting signal methods (OLS-AR + Bayesian-prior variant).

Both methods share the same workflow:

1. For each configured release, load the target FRED series + its
   lead indicator series via the standard data loaders.
2. Align the lead indicators to the same monthly / quarterly cadence
   as the target by taking the most recent value at-or-before each
   target observation.
3. Build a design matrix [intercept, lagged_target, lead_indicators]
   and fit (OLS for baseline, conjugate Normal-Inverse-Gamma for
   BVAR).
4. At inference time, look up today's covariates and predict the
   *next* release. Compute surprise = predicted - consensus (when
   available; otherwise zero) and standardise.
5. Emit one ``SignalOutput`` per affected instrument with the
   surprise as raw_value (tanh-squashed).

The two methods differ only in the fit step. The data loading,
alignment, and inference pipeline is shared.
"""

# Statistical convention uses uppercase X / V_0 for design matrix /
# prior covariance. Suppress N806 file-wide.
# ruff: noqa: N806

from __future__ import annotations

import pickle
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Self

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from macro_trader.data.loaders import load_macro_series
from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.nowcasting.bvar import (
    fit_bayesian_regression,
    minnesota_prior_v0,
)
from macro_trader.signals.nowcasting.releases import (
    DEFAULT_RELEASES,
    ReleaseSpec,
)
from macro_trader.signals.output import cross_sectional_rank

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def _load_release_panel(
    session: Session, spec: ReleaseSpec, *, as_of: datetime, lookback_releases: int
) -> pd.DataFrame | None:
    """Build a panel with columns [target, lead_indicator_0, ...]
    aligned on the target's value_ts. Returns None if the target
    series is empty."""
    lookback_days = lookback_releases * 365
    start = as_of - timedelta(days=lookback_days)
    target_series = load_macro_series(
        session, spec.target_fred, start=start, end=as_of, as_of=as_of
    )
    if target_series.empty:
        return None
    panel = pd.DataFrame({"target": target_series})
    for lead in spec.lead_indicators:
        lead_series = load_macro_series(
            session, lead, start=start, end=as_of, as_of=as_of
        )
        if lead_series.empty:
            continue
        # Align lead indicator to target dates by carrying forward
        # the latest available value before each target observation.
        merged = pd.concat([target_series.rename("target"), lead_series.rename(lead)], axis=1)
        merged = merged.sort_index().ffill().loc[target_series.index]
        panel[lead] = merged[lead]
    return panel.dropna(how="any")


def _build_design_matrix(panel: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Construct (X, y) for the regression. X columns:
    [intercept, target_lag1, lead_indicator_0_lag1, ...]."""
    target = panel["target"].astype(float)
    lags = panel.shift(1).dropna(how="any")
    target = target.loc[lags.index]

    n = len(target)
    p = 1 + lags.shape[1]  # intercept + lag columns
    X = np.zeros((n, p))
    X[:, 0] = 1.0
    X[:, 1:] = lags.values
    y = target.values
    return X, y


def _materialise_nowcasting_outputs(
    *,
    per_instrument_score: dict[str, float],
    per_instrument_conf: dict[str, float],
    per_instrument_meta: dict[str, dict[str, Any]],
    data: SignalInput,
    method_id: str,
) -> list[SignalOutput]:
    if not per_instrument_score:
        return []
    ranks = cross_sectional_rank(
        {k: abs(v) for k, v in per_instrument_score.items() if v != 0.0}
    )
    outputs: list[SignalOutput] = []
    for inst, score in per_instrument_score.items():
        outputs.append(
            SignalOutput(
                instrument_id=inst,
                value_ts=data.as_of,
                observation_ts=data.as_of,
                raw_value=float(np.tanh(score)),
                zscore=float(score),
                rank=float(ranks.get(inst, 0.5)),
                confidence=float(per_instrument_conf.get(inst, 0.05)),
                rolling_sharpe_252=None,
                metadata={"method_id": method_id, **per_instrument_meta.get(inst, {})},
            )
        )
    return outputs


class _NowcastingBase(SignalMethod):
    """Shared scaffolding: per-release fit + inference."""

    USE_BAYESIAN: bool = False

    def __init__(
        self,
        *,
        releases: tuple[ReleaseSpec, ...] = DEFAULT_RELEASES,
        lookback_releases: int = 60,
    ) -> None:
        self.releases = releases
        self.lookback_releases = int(lookback_releases)
        self._state: dict[str, Any] | None = None

    def fit_on_session(
        self, session: Session, as_of: object, instrument_ids: list[str]
    ) -> None:
        as_of_dt = (
            as_of if isinstance(as_of, datetime) else datetime.fromisoformat(str(as_of))
        )
        per_release: dict[str, dict[str, Any]] = {}
        for spec in self.releases:
            panel = _load_release_panel(
                session, spec, as_of=as_of_dt, lookback_releases=self.lookback_releases
            )
            if panel is None or panel.shape[0] < 6:  # need a few observations
                continue
            X, y = _build_design_matrix(panel)
            if X.shape[0] < 6:
                continue
            try:
                if self.USE_BAYESIAN:
                    # Own-lag column is index 1 (after intercept).
                    beta_0, V_0 = minnesota_prior_v0(
                        n_features=X.shape[1],
                        own_lag_index=1,
                        intercept_index=0,
                    )
                    result = fit_bayesian_regression(X, y, beta_0=beta_0, V_0=V_0)
                    last_row = X[-1, :]
                    pred_mean, pred_var = result.predict(last_row)
                    fit_summary = {
                        "method": "bvar",
                        "beta_mean": result.beta_mean.tolist(),
                        "sigma2_posterior_mean": result.sigma2_mean,
                        "pred_mean": pred_mean,
                        "pred_var": pred_var,
                        "last_actual": float(y[-1]),
                        "columns": ["intercept", "target_lag", *spec.lead_indicators],
                    }
                else:
                    model = LinearRegression(fit_intercept=False)
                    model.fit(X, y)
                    r2 = float(model.score(X, y))
                    last_row = X[-1:].copy()
                    pred_mean = float(model.predict(last_row)[0])
                    fit_summary = {
                        "method": "ols_ar",
                        "betas": model.coef_.tolist(),
                        "r2": r2,
                        "pred_mean": pred_mean,
                        "last_actual": float(y[-1]),
                        "columns": ["intercept", "target_lag", *spec.lead_indicators],
                    }
                per_release[spec.release_id] = fit_summary
            except Exception as exc:  # pragma: no cover - numpy edge case
                log.warning(
                    "signals.nowcasting.fit_failed",
                    release=spec.release_id,
                    error=str(exc),
                )
                continue

        if not per_release:
            self._state = None
            return

        self._state = {
            "per_release": per_release,
            "fit_as_of": as_of_dt.isoformat(),
            "instruments": list(instrument_ids),
        }

    def serialize(self) -> bytes:
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL) if self._state else b""

    @classmethod
    def deserialize(cls, blob: bytes) -> Self:
        m = cls()
        if blob:
            m._state = pickle.loads(blob)
        return m

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError(f"{type(self).__name__} requires a DB session")
        if self._state is None:
            log.info("signals.nowcasting.fallback_fit")
            self.fit_on_session(session, data.as_of, list(data.instrument_ids))
            if self._state is None:
                return []

        per_instrument_score: dict[str, float] = {}
        per_instrument_conf: dict[str, float] = {}
        per_instrument_meta: dict[str, dict[str, Any]] = {}

        # Aggregate per-release surprises onto affected instruments.
        # Stage 5 fallback when calendar consensus isn't available:
        # surprise = (pred_mean - last_actual) / sqrt(pred_var or 1).
        for spec in self.releases:
            fit = self._state["per_release"].get(spec.release_id)
            if fit is None:
                continue
            pred = float(fit["pred_mean"])
            last = float(fit["last_actual"])
            denom = float(fit.get("pred_var", 1.0)) ** 0.5 if "pred_var" in fit else None
            if denom is None or denom <= 0:
                denom = max(abs(last) * 0.05, 1e-6)
            z = float(np.clip((pred - last) / denom, -3.0, 3.0))

            for inst in spec.affected_instruments:
                per_instrument_score[inst] = per_instrument_score.get(inst, 0.0) + z
                per_instrument_conf[inst] = max(
                    per_instrument_conf.get(inst, 0.0), 0.5
                )
                per_instrument_meta.setdefault(inst, {}).setdefault(
                    "releases", []
                ).append(spec.release_id)

        # Uncovered instruments: zero / no-data.
        for inst in data.instrument_ids:
            if inst not in per_instrument_score:
                per_instrument_score[inst] = 0.0
                per_instrument_conf[inst] = 0.0
                per_instrument_meta[inst] = {"covered": False}

        return _materialise_nowcasting_outputs(
            per_instrument_score=per_instrument_score,
            per_instrument_conf=per_instrument_conf,
            per_instrument_meta=per_instrument_meta,
            data=data,
            method_id=self.metadata.method_id,
        )


# ----------------------------------------------------------------------
# OLS-AR baseline
# ----------------------------------------------------------------------
class OLSARNowcaster(_NowcastingBase):
    """OLS regression of release on lagged release + lead indicators."""

    USE_BAYESIAN = False

    metadata = MethodMetadata(
        method_id="nowcasting.ols_ar.v1",
        component="nowcasting_signal",
        name="OLS-AR Nowcaster",
        version="1.0.0",
        description=(
            "Linear regression nowcast with AR(1) target + lead-indicator "
            "covariates per release."
        ),
        references=[
            "Giannone, Reichlin, Small (2008) Nowcasting: The Real-Time Informational Content"
        ],
    )


# ----------------------------------------------------------------------
# Bayesian-prior shadow
# ----------------------------------------------------------------------
class BVARNowcaster(_NowcastingBase):
    """Bayesian linear regression with Minnesota-flavoured prior.

    Univariate analogue: own-lag shrunk toward 1 (random walk), lead-
    indicator coefficients shrunk toward 0, intercept unrestricted.
    Uses the analytical Normal-Inverse-Gamma conjugate posterior.
    """

    USE_BAYESIAN = True

    metadata = MethodMetadata(
        method_id="nowcasting.bvar.v1",
        component="nowcasting_signal",
        name="Bayesian Nowcaster",
        version="1.0.0",
        description=(
            "Per-release Bayesian linear regression with Minnesota-flavoured "
            "prior: own-lag shrunk to 1, lead-indicator coefficients shrunk "
            "to 0. Conjugate Normal-Inverse-Gamma posterior."
        ),
        references=[
            "Litterman (1986) Forecasting with Bayesian Vector Autoregressions",
            "Banbura, Giannone, Reichlin (2010) Large Bayesian VARs",
        ],
    )


__all__ = ["BVARNowcaster", "OLSARNowcaster"]
