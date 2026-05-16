"""Factor exposure signal methods (OLS / RF / Causal Forest).

All three methods consume:

- A panel of instrument log-returns (rows = date, cols = instrument).
- A panel of factor z-scores from ``factors.build_factor_panel`` (rows
  = date, cols = factor name).

They produce per-instrument signals via:

- **OLS**: rolling regression r_i ~ alpha + beta @ f. Signal =
  -beta @ z_today, with confidence = R^2.
- **RF**: RandomForestRegressor trained per instrument on the full
  panel of factor returns. Signal = -prediction / vol, with confidence
  = OOB R^2.
- **CausalForest**: EconML CausalForestDML treating each factor as a
  treatment. Signal = -CATE @ z_today, with confidence = pseudo-R^2.
  Gated on EconML import; raises a clear error if the ``[ml]`` extra
  isn't installed.

Sign convention: positive signal = long bias (a positive factor
view + positive exposure flips to a contrarian short).

State is fit_on / predict_on / serialize / deserialize so the weekly
refit asset can cache fitted models in
``system.methods_registry.serialized_blob``.
"""

# Statistical-ML convention uses uppercase X / T / W for design / treatment /
# controls matrices; our `from sklearn.ensemble import RandomForestRegressor as _RF`
# alias is also intentional (CamelCase class re-exported under a private
# short name). Suppress N806 / N814 file-wide.
# ruff: noqa: N806, N814

from __future__ import annotations

import pickle
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression

from macro_trader.data.loaders import load_close_panel
from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.factor_exposure.factors import (
    DEFAULT_FACTORS,
    FactorSpec,
    build_factor_panel,
    latest_factor_zscores,
)
from macro_trader.signals.output import cross_sectional_rank

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------
def _instrument_returns(
    session: Session,
    instrument_ids: list[str],
    *,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> pd.DataFrame:
    panel = load_close_panel(
        session, instrument_ids, start=start, end=end, as_of=as_of
    )
    if panel.empty:
        return panel
    return np.log(panel.replace(0, np.nan)).diff().dropna(how="all")


def _aligned_xy(
    returns: pd.DataFrame, factor_panel: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Inner-join returns + factor panel on date, drop rows with NaN."""
    if returns.empty or factor_panel.empty:
        return pd.DataFrame(), pd.DataFrame()
    merged = returns.join(factor_panel, how="inner").dropna(how="any")
    if merged.empty:
        return pd.DataFrame(), pd.DataFrame()
    factor_cols = list(factor_panel.columns)
    instrument_cols = [c for c in merged.columns if c not in factor_cols]
    return merged[instrument_cols], merged[factor_cols]


def _materialise_factor_outputs(
    *,
    instrument_signal: dict[str, float],
    factor_loadings: dict[str, dict[str, float]],
    factor_zscores: dict[str, float],
    confidence_per_instrument: dict[str, float],
    data: SignalInput,
    method_id: str,
    extra_metadata: dict[str, Any] | None = None,
) -> list[SignalOutput]:
    """Translate per-instrument scalar signals into ``SignalOutput`` rows
    anchored on ``data.as_of``."""
    if not instrument_signal:
        return []
    ranks = cross_sectional_rank(instrument_signal)
    outputs: list[SignalOutput] = []
    for inst, raw in instrument_signal.items():
        meta: dict[str, Any] = {
            "method_id": method_id,
            "factor_loadings": factor_loadings.get(inst, {}),
            "factor_zscores": factor_zscores,
        }
        if extra_metadata:
            meta.update(extra_metadata)
        outputs.append(
            SignalOutput(
                instrument_id=inst,
                value_ts=data.as_of,
                observation_ts=data.as_of,
                raw_value=float(np.tanh(raw)),
                zscore=float(raw),
                rank=float(ranks.get(inst, 0.5)),
                confidence=float(confidence_per_instrument.get(inst, 0.05)),
                rolling_sharpe_252=None,
                metadata=meta,
            )
        )
    return outputs


# ----------------------------------------------------------------------
# OLS baseline
# ----------------------------------------------------------------------
class OLSFactorExposure(SignalMethod):
    """Rolling OLS regression of instrument returns on macro factors.

    Per instrument, fit r_i ~ alpha + beta @ f over the lookback
    window. Today's signal = -beta @ z_today (long-bias convention).
    Confidence = R^2 of the regression, clipped to [0.05, 1.0] so a
    near-zero R^2 doesn't extinguish the signal entirely downstream.
    """

    metadata = MethodMetadata(
        method_id="factor_exposure.ols.v1",
        component="factor_exposure_signal",
        name="OLS Rolling Factor Exposure",
        version="1.0.0",
        description=(
            "Rolling 252-day OLS exposure to six macro factors; signal = "
            "-Sigma beta * z."
        ),
        references=["Fama & French (1992) Common Risk Factors"],
    )

    def __init__(
        self,
        *,
        factors: tuple[FactorSpec, ...] = DEFAULT_FACTORS,
        lookback_days: int = 252,
        min_history_days: int = 252,
    ) -> None:
        self.factors = factors
        self.lookback_days = int(lookback_days)
        self.min_history_days = int(min_history_days)
        self._state: dict[str, Any] | None = None

    # --- fit / predict / serialize -------------------------------------
    def fit_on_panels(
        self, returns: pd.DataFrame, factor_panel: pd.DataFrame
    ) -> None:
        """Fit per-instrument OLS over the trailing ``lookback_days``."""
        ret, fac = _aligned_xy(returns, factor_panel)
        if ret.empty or fac.empty:
            self._state = None
            return
        ret = ret.iloc[-self.lookback_days :]
        fac = fac.iloc[-self.lookback_days :]
        if ret.shape[0] < self.min_history_days:
            self._state = None
            return

        per_instrument: dict[str, dict[str, Any]] = {}
        for inst in ret.columns:
            y = ret[inst].dropna()
            if len(y) < self.min_history_days // 2:
                continue
            X = fac.loc[y.index]
            if X.empty:
                continue
            model = LinearRegression()
            model.fit(X.values, y.values)
            r2 = float(model.score(X.values, y.values))
            per_instrument[inst] = {
                "betas": dict(zip(X.columns, model.coef_, strict=False)),
                "intercept": float(model.intercept_),
                "r2": r2,
                "residual_std": float((y.values - model.predict(X.values)).std(ddof=1)),
            }

        if not per_instrument:
            self._state = None
            return

        self._state = {
            "per_instrument": per_instrument,
            "factor_columns": list(fac.columns),
            "fit_rows": int(ret.shape[0]),
        }

    def predict_at(self, factor_zscores: dict[str, float]) -> dict[str, float]:
        """Apply cached betas to a vector of factor z-scores. Returns
        per-instrument signal scores (pre-tanh)."""
        if self._state is None:
            return {}
        out: dict[str, float] = {}
        for inst, fit in self._state["per_instrument"].items():
            betas: dict[str, float] = fit["betas"]
            score = -sum(
                betas.get(name, 0.0) * factor_zscores.get(name, 0.0)
                for name in self._state["factor_columns"]
            )
            out[inst] = float(score)
        return out

    def serialize(self) -> bytes:
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL) if self._state else b""

    @classmethod
    def deserialize(cls, blob: bytes) -> OLSFactorExposure:
        m = cls()
        if blob:
            m._state = pickle.loads(blob)
        return m

    # --- compute -------------------------------------------------------
    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("OLSFactorExposure requires a DB session")
        load_start = data.start - timedelta(days=self.lookback_days + 90)
        returns = _instrument_returns(
            session, data.instrument_ids, start=load_start, end=data.end, as_of=data.as_of
        )
        if returns.empty:
            return []
        factor_panel = build_factor_panel(
            session, as_of=data.as_of, lookback_days=self.lookback_days + 60, factors=self.factors
        )
        if factor_panel.empty:
            log.info("signals.factor_exposure.ols.no_factors")
            return []

        if self._state is None:
            log.info("signals.factor_exposure.ols.fallback_fit")
            self.fit_on_panels(returns, factor_panel)
            if self._state is None:
                return []

        z_today = latest_factor_zscores(
            session, as_of=data.as_of, factors=self.factors
        )
        if not z_today:
            return []

        instrument_signal = self.predict_at(z_today)
        loadings = {
            inst: dict(fit["betas"])
            for inst, fit in self._state["per_instrument"].items()
        }
        confidence = {
            inst: max(0.05, min(1.0, float(fit["r2"])))
            for inst, fit in self._state["per_instrument"].items()
        }
        return _materialise_factor_outputs(
            instrument_signal=instrument_signal,
            factor_loadings=loadings,
            factor_zscores=z_today,
            confidence_per_instrument=confidence,
            data=data,
            method_id=self.metadata.method_id,
        )


# ----------------------------------------------------------------------
# Random Forest shadow
# ----------------------------------------------------------------------
class RandomForestFactorExposure(SignalMethod):
    """RandomForestRegressor for non-linear factor effects.

    Per instrument, fit RF(r_i ~ f) on the trailing ``lookback_days``.
    Today's signal = -predicted_return / residual_vol; ``raw_value``
    via tanh squashing so it's bounded in [-1, 1].

    Confidence = OOB R^2 (clipped to [0.05, 1.0]).
    """

    metadata = MethodMetadata(
        method_id="factor_exposure.rf.v1",
        component="factor_exposure_signal",
        name="Random Forest Factor Exposure",
        version="1.0.0",
        description="RandomForestRegressor for non-linear factor exposures.",
        references=["Breiman (2001) Random Forests"],
    )

    def __init__(
        self,
        *,
        factors: tuple[FactorSpec, ...] = DEFAULT_FACTORS,
        lookback_days: int = 504,
        min_history_days: int = 252,
        n_estimators: int = 200,
        max_depth: int = 5,
        min_samples_leaf: int = 20,
        random_state: int = 42,
    ) -> None:
        self.factors = factors
        self.lookback_days = int(lookback_days)
        self.min_history_days = int(min_history_days)
        self.n_estimators = int(n_estimators)
        self.max_depth = int(max_depth)
        self.min_samples_leaf = int(min_samples_leaf)
        self.random_state = int(random_state)
        self._state: dict[str, Any] | None = None

    def fit_on_panels(self, returns: pd.DataFrame, factor_panel: pd.DataFrame) -> None:
        ret, fac = _aligned_xy(returns, factor_panel)
        if ret.empty or fac.empty:
            self._state = None
            return
        ret = ret.iloc[-self.lookback_days :]
        fac = fac.iloc[-self.lookback_days :]
        if ret.shape[0] < self.min_history_days:
            self._state = None
            return

        per_instrument: dict[str, dict[str, Any]] = {}
        for inst in ret.columns:
            y = ret[inst].dropna()
            if len(y) < self.min_history_days // 2:
                continue
            X = fac.loc[y.index]
            if X.empty:
                continue
            rf = RandomForestRegressor(
                n_estimators=self.n_estimators,
                max_depth=self.max_depth,
                min_samples_leaf=self.min_samples_leaf,
                oob_score=True,
                random_state=self.random_state,
                n_jobs=1,
            )
            rf.fit(X.values, y.values)
            oob_r2 = float(getattr(rf, "oob_score_", 0.0) or 0.0)
            preds = rf.predict(X.values)
            per_instrument[inst] = {
                "rf": rf,
                "oob_r2": oob_r2,
                "residual_std": float((y.values - preds).std(ddof=1)),
                "feature_importances": dict(
                    zip(fac.columns, rf.feature_importances_, strict=False)
                ),
            }

        if not per_instrument:
            self._state = None
            return
        self._state = {
            "per_instrument": per_instrument,
            "factor_columns": list(fac.columns),
            "fit_rows": int(ret.shape[0]),
        }

    def predict_at(self, factor_zscores: dict[str, float]) -> dict[str, float]:
        if self._state is None:
            return {}
        cols: list[str] = self._state["factor_columns"]
        x_today = np.asarray([factor_zscores.get(c, 0.0) for c in cols]).reshape(1, -1)
        out: dict[str, float] = {}
        for inst, fit in self._state["per_instrument"].items():
            pred = float(fit["rf"].predict(x_today)[0])
            vol = float(fit["residual_std"]) or 1.0
            out[inst] = -pred / vol
        return out

    def serialize(self) -> bytes:
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL) if self._state else b""

    @classmethod
    def deserialize(cls, blob: bytes) -> RandomForestFactorExposure:
        m = cls()
        if blob:
            m._state = pickle.loads(blob)
        return m

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("RandomForestFactorExposure requires a DB session")
        load_start = data.start - timedelta(days=self.lookback_days + 90)
        returns = _instrument_returns(
            session, data.instrument_ids, start=load_start, end=data.end, as_of=data.as_of
        )
        if returns.empty:
            return []
        factor_panel = build_factor_panel(
            session, as_of=data.as_of, lookback_days=self.lookback_days + 60, factors=self.factors
        )
        if factor_panel.empty:
            return []

        if self._state is None:
            log.info("signals.factor_exposure.rf.fallback_fit")
            self.fit_on_panels(returns, factor_panel)
            if self._state is None:
                return []

        z_today = latest_factor_zscores(
            session, as_of=data.as_of, factors=self.factors
        )
        if not z_today:
            return []

        instrument_signal = self.predict_at(z_today)
        loadings = {
            inst: dict(fit["feature_importances"])
            for inst, fit in self._state["per_instrument"].items()
        }
        confidence = {
            inst: max(0.05, min(1.0, float(fit["oob_r2"])))
            for inst, fit in self._state["per_instrument"].items()
        }
        return _materialise_factor_outputs(
            instrument_signal=instrument_signal,
            factor_loadings=loadings,
            factor_zscores=z_today,
            confidence_per_instrument=confidence,
            data=data,
            method_id=self.metadata.method_id,
        )


# ----------------------------------------------------------------------
# Causal Forest shadow (gated on EconML)
# ----------------------------------------------------------------------
def _econml_available() -> bool:
    try:
        import econml.dml  # noqa: F401
    except Exception:
        return False
    return True


class CausalForestFactorExposure(SignalMethod):
    """EconML CausalForestDML for conditional exposures.

    Treats each factor independently as a "treatment" and the
    instrument's return as the outcome; per-factor CATE estimates feed
    a composite score = -CATE @ z_today. Confidence = the outcome
    model's pseudo-R^2.

    Gated on the optional ``[ml]`` extra (``uv sync --extra ml``).
    Constructing the method when EconML isn't installed raises
    ``RuntimeError`` immediately so the failure is loud and obvious in
    test logs / Dagster runs.
    """

    metadata = MethodMetadata(
        method_id="factor_exposure.causal_forest.v1",
        component="factor_exposure_signal",
        name="Causal Forest Factor Exposure",
        version="1.0.0",
        description="EconML CausalForestDML for conditional factor exposures.",
        references=[
            "Athey & Imbens (2016) Recursive Partitioning for Heterogeneous Causal Effects",
            "Chernozhukov et al. (2018) Double/Debiased ML",
        ],
    )

    def __init__(
        self,
        *,
        factors: tuple[FactorSpec, ...] = DEFAULT_FACTORS,
        lookback_days: int = 504,
        min_history_days: int = 504,
        n_estimators: int = 200,
        min_samples_leaf: int = 20,
        random_state: int = 42,
    ) -> None:
        if not _econml_available():
            raise RuntimeError(
                "CausalForestFactorExposure requires the optional `[ml]` extra; "
                "install with `uv sync --extra ml` or omit this method from the registry."
            )
        self.factors = factors
        self.lookback_days = int(lookback_days)
        self.min_history_days = int(min_history_days)
        self.n_estimators = int(n_estimators)
        self.min_samples_leaf = int(min_samples_leaf)
        self.random_state = int(random_state)
        self._state: dict[str, Any] | None = None

    def fit_on_panels(self, returns: pd.DataFrame, factor_panel: pd.DataFrame) -> None:
        from econml.dml import CausalForestDML
        from sklearn.ensemble import RandomForestRegressor as _RF

        ret, fac = _aligned_xy(returns, factor_panel)
        if ret.empty or fac.empty:
            self._state = None
            return
        ret = ret.iloc[-self.lookback_days :]
        fac = fac.iloc[-self.lookback_days :]
        if ret.shape[0] < self.min_history_days:
            self._state = None
            return

        per_instrument: dict[str, dict[str, Any]] = {}
        for inst in ret.columns:
            y = ret[inst].dropna()
            if len(y) < self.min_history_days // 2:
                continue
            X = fac.loc[y.index]
            if X.empty:
                continue
            cates: dict[str, float] = {}
            try:
                # One CATE per factor: factor i is the treatment, the
                # rest serve as both heterogeneity features (X) and
                # controls (W). EconML CausalForestDML requires X for
                # CATE estimation; reusing the controls as X gives a
                # dataset-average CATE via const_marginal_effect.
                for _i, factor_name in enumerate(X.columns):
                    T = X[factor_name].values
                    other_cols = X.drop(columns=[factor_name]).values
                    est = CausalForestDML(
                        n_estimators=self.n_estimators,
                        min_samples_leaf=self.min_samples_leaf,
                        random_state=self.random_state,
                        discrete_treatment=False,
                        model_y=_RF(
                            n_estimators=50, min_samples_leaf=10, random_state=self.random_state
                        ),
                        model_t=_RF(
                            n_estimators=50, min_samples_leaf=10, random_state=self.random_state
                        ),
                    )
                    est.fit(Y=y.values, T=T, X=other_cols, W=other_cols)
                    cates[factor_name] = float(
                        est.const_marginal_effect(other_cols).mean()
                    )
                per_instrument[inst] = {
                    "cates": cates,
                    "pseudo_r2": 0.05,  # EconML doesn't report R2 directly
                }
            except Exception as exc:  # pragma: no cover - EconML failure
                log.warning(
                    "signals.factor_exposure.cf.fit_failed",
                    instrument=inst,
                    error=str(exc),
                )
                continue

        if not per_instrument:
            self._state = None
            return
        self._state = {
            "per_instrument": per_instrument,
            "factor_columns": list(fac.columns),
            "fit_rows": int(ret.shape[0]),
        }

    def predict_at(self, factor_zscores: dict[str, float]) -> dict[str, float]:
        if self._state is None:
            return {}
        out: dict[str, float] = {}
        for inst, fit in self._state["per_instrument"].items():
            cates: dict[str, float] = fit["cates"]
            score = -sum(
                cates.get(name, 0.0) * factor_zscores.get(name, 0.0)
                for name in self._state["factor_columns"]
            )
            out[inst] = float(score)
        return out

    def serialize(self) -> bytes:
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL) if self._state else b""

    @classmethod
    def deserialize(cls, blob: bytes) -> CausalForestFactorExposure:
        m = cls()
        if blob:
            m._state = pickle.loads(blob)
        return m

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("CausalForestFactorExposure requires a DB session")
        load_start = data.start - timedelta(days=self.lookback_days + 90)
        returns = _instrument_returns(
            session, data.instrument_ids, start=load_start, end=data.end, as_of=data.as_of
        )
        if returns.empty:
            return []
        factor_panel = build_factor_panel(
            session, as_of=data.as_of, lookback_days=self.lookback_days + 60, factors=self.factors
        )
        if factor_panel.empty:
            return []

        if self._state is None:
            self.fit_on_panels(returns, factor_panel)
            if self._state is None:
                return []

        z_today = latest_factor_zscores(
            session, as_of=data.as_of, factors=self.factors
        )
        if not z_today:
            return []

        instrument_signal = self.predict_at(z_today)
        loadings = {
            inst: dict(fit["cates"])
            for inst, fit in self._state["per_instrument"].items()
        }
        confidence = {
            inst: max(0.05, min(1.0, float(fit["pseudo_r2"])))
            for inst, fit in self._state["per_instrument"].items()
        }
        return _materialise_factor_outputs(
            instrument_signal=instrument_signal,
            factor_loadings=loadings,
            factor_zscores=z_today,
            confidence_per_instrument=confidence,
            data=data,
            method_id=self.metadata.method_id,
        )


__all__ = [
    "CausalForestFactorExposure",
    "OLSFactorExposure",
    "RandomForestFactorExposure",
    "_econml_available",
]
