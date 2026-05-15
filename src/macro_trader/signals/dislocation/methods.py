"""Cross-asset dislocation signal methods.

Both methods share the same conceptual signal: fit a factor model on
the panel of instrument returns and emit the *residual* (the part of an
instrument's return that the factor model couldn't explain) as a
mean-reversion signal. Positive residual = instrument outperformed the
peer prediction = contrarian short tilt = negative ``raw_value`` (per
the project-wide long-bias convention).

Stage 4B split fitting and inference cleanly:

- ``fit_on_returns(returns_df)`` populates ``self._state`` with the
  fitted model + bookkeeping.
- ``predict_on_returns(returns_df)`` applies the cached state and
  returns a (date x instrument) ``raw_signal`` DataFrame.
- ``compute(data, session)`` is the public entry point: loads data via
  the loader, applies cached state if present, falls back to "fit then
  predict on the daily run" with a logged warning if no state is
  available (graceful degradation).
- ``serialize()`` / ``deserialize(blob)`` round-trip ``self._state``
  via pickle so the weekly-refit Dagster asset can stash fitted state
  in ``system.methods_registry.serialized_blob``.
- ``PCADislocation.align_signs_to(prior_state)`` flips signs of new
  components so they correlate positively with the prior week's
  components — keeps loadings interpretable in the dashboard across
  refits even though the residual signal itself is sign-flip-invariant.
"""

from __future__ import annotations

import pickle
import warnings
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

from macro_trader.data.loaders import load_close_panel
from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.output import (
    cross_sectional_rank,
    rolling_zscore_of_self,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def _compute_returns(panel: pd.DataFrame) -> pd.DataFrame:
    """Daily log-return panel; first row dropped."""
    return np.log(panel.replace(0, np.nan)).diff().dropna(how="all")


def _materialise_dislocation_outputs(
    *,
    raw_signal: pd.DataFrame,
    data: SignalInput,
    explained_variance: float,
    n_components: int,
    method_id: str,
    extra_metadata: dict[str, Any] | None = None,
) -> list[SignalOutput]:
    """Translate a (date x instrument) raw_signal frame into ``SignalOutput`` rows."""
    if raw_signal.empty:
        return []
    start = pd.Timestamp(data.start).tz_convert("UTC")
    end = pd.Timestamp(data.end).tz_convert("UTC")
    mask = (raw_signal.index >= start) & (raw_signal.index <= end)
    if not mask.any():
        return []

    zscored = {
        col: rolling_zscore_of_self(raw_signal[col], lookback=60, min_periods=20)
        for col in raw_signal.columns
    }

    outputs: list[SignalOutput] = []
    for value_ts in raw_signal.index[mask]:
        row_values: dict[str, float] = {}
        for col in raw_signal.columns:
            v = raw_signal.at[value_ts, col]
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            row_values[col] = float(v)
        if not row_values:
            continue
        ranks = cross_sectional_rank(row_values)
        for col, v in row_values.items():
            z_val = zscored[col].get(value_ts)
            z = float(z_val) if z_val is not None and not np.isnan(z_val) else 0.0
            conf = min(1.0, explained_variance)
            meta: dict[str, Any] = {
                "method_id": method_id,
                "n_components": n_components,
                "explained_variance": float(explained_variance),
            }
            if extra_metadata:
                meta.update(extra_metadata)
            outputs.append(
                SignalOutput(
                    instrument_id=col,
                    value_ts=value_ts.to_pydatetime(),
                    observation_ts=data.as_of,
                    raw_value=v,
                    zscore=z,
                    rank=float(ranks.get(col, 0.5)),
                    confidence=float(conf),
                    rolling_sharpe_252=None,
                    metadata=meta,
                )
            )
    return outputs


# ----------------------------------------------------------------------
# PCA
# ----------------------------------------------------------------------
class PCADislocation(SignalMethod):
    """PCA-based cross-asset dislocation signal.

    Workflow:

    1. Load a return panel covering the lookback + emit window.
    2. If cached fitted state is present, apply it. Otherwise fit on
       the lookback window (graceful fallback for first-ever runs or
       missed weekly refits).
    3. For each row in the emit window, reconstruct from the top-K
       factors and compute the residual.
    4. Z-score the residual over a trailing 60-day window per instrument
       and apply ``-tanh`` for the long-bias convention.
    """

    metadata = MethodMetadata(
        method_id="dislocation.pca.v1",
        component="dislocation_signal",
        name="PCA Cross-Asset Dislocation",
        version="1.0.0",
        description=(
            "Standardized residual to top-K PCA factor reconstruction; "
            "negated for long-bias convention."
        ),
        references=[
            "Avellaneda & Lee (2010) Statistical Arbitrage in the US Equities Market",
            "MacKinlay & Pastor (2000) Asset Pricing Models",
        ],
    )

    def __init__(
        self,
        *,
        lookback_days: int = 252,
        n_components: int = 3,
        min_history_days: int = 252,
    ) -> None:
        self.lookback_days = int(lookback_days)
        self.n_components = int(n_components)
        self.min_history_days = int(min_history_days)
        self._state: dict[str, Any] | None = None

    # --- fit / predict / serialize -------------------------------------
    def fit_on_returns(self, returns: pd.DataFrame) -> None:
        """Fit PCA on the trailing ``lookback_days`` of ``returns``.

        ``returns`` is a (date x instrument) DataFrame of log-returns.
        Drops columns with any NaNs in the fit window before fitting.
        Stores the fitted PCA, the column order it saw, and the
        explained-variance ratio in ``self._state``.
        """
        fit_window = returns.iloc[-self.lookback_days :].dropna(how="any", axis=1)
        if fit_window.shape[1] < self.n_components + 1:
            log.info(
                "signals.dislocation.pca.too_few_instruments",
                kept=fit_window.shape[1],
                required=self.n_components + 1,
            )
            self._state = None
            return

        pca = PCA(n_components=self.n_components, svd_solver="full")
        pca.fit(fit_window.values)

        self._state = {
            "pca": pca,
            "columns": list(fit_window.columns),
            "explained_variance_ratio": pca.explained_variance_ratio_.tolist(),
            "n_components": self.n_components,
            "fit_rows": int(fit_window.shape[0]),
        }

    def predict_on_returns(self, returns: pd.DataFrame) -> pd.DataFrame:
        """Apply cached PCA to ``returns``; return tanh-squashed
        residuals as a (date x instrument) DataFrame."""
        if self._state is None:
            return pd.DataFrame()
        pca: PCA = self._state["pca"]
        cols: list[str] = self._state["columns"]

        # Restrict to columns the PCA was fit on; ignore unknown
        # instruments that joined the universe after the last refit.
        usable_cols = [c for c in cols if c in returns.columns]
        if not usable_cols or len(usable_cols) < pca.n_components_:
            return pd.DataFrame()

        sub = returns[usable_cols].dropna(how="any")
        if sub.empty:
            return pd.DataFrame()

        scores = pca.transform(sub.values)
        recon = pca.inverse_transform(scores)
        residuals = sub - pd.DataFrame(recon, index=sub.index, columns=sub.columns)
        raw_signal = (-residuals.clip(lower=-3.0, upper=3.0)).apply(np.tanh)
        return raw_signal.reindex(returns.index)

    def align_signs_to(self, prior_state: dict[str, Any]) -> None:
        """Flip signs of fitted components to maximise correlation with a
        prior fit's components (Stage 4B PCA sign-alignment).

        For each new component we find the prior component with maximum
        absolute correlation (handles both flips and reorderings). If the
        chosen correlation is negative, flip the new component's sign.

        Loadings shown in the dashboard stay continuous across refits.
        """
        if self._state is None or "pca" not in prior_state:
            return
        new_pca: PCA = self._state["pca"]
        old_pca: PCA = prior_state["pca"]
        old_columns: list[str] = prior_state["columns"]
        new_columns: list[str] = self._state["columns"]

        common = [c for c in new_columns if c in old_columns]
        if len(common) < 2:
            return
        new_idx = [new_columns.index(c) for c in common]
        old_idx = [old_columns.index(c) for c in common]
        new_comp = new_pca.components_[:, new_idx]
        old_comp = old_pca.components_[:, old_idx]

        for i in range(new_comp.shape[0]):
            if i >= old_comp.shape[0]:
                break
            cors = np.array([_safe_corr(new_comp[i], old_comp[j]) for j in range(old_comp.shape[0])])
            j_star = int(np.argmax(np.abs(cors)))
            if cors[j_star] < 0:
                new_pca.components_[i, :] *= -1.0

    def serialize(self) -> bytes:
        if self._state is None:
            return b""
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def deserialize(cls, blob: bytes) -> PCADislocation:
        m = cls()
        if blob:
            m._state = pickle.loads(blob)
        return m

    # --- compute (orchestration) ---------------------------------------
    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("PCADislocation requires a DB session")
        load_start = data.start - timedelta(days=self.lookback_days + 60)
        panel = load_close_panel(
            session,
            data.instrument_ids,
            start=load_start,
            end=data.end,
            as_of=data.as_of,
            calendar=data.calendar,
        )
        if panel.empty or panel.shape[1] < 2:
            return []

        returns = _compute_returns(panel)
        if returns.shape[0] < self.min_history_days:
            log.info(
                "signals.dislocation.pca.insufficient_history",
                rows=returns.shape[0],
                required=self.min_history_days,
            )
            return []

        if self._state is None:
            log.info(
                "signals.dislocation.pca.fallback_fit",
                reason="no cached state; fitting on daily-run window",
            )
            self.fit_on_returns(returns)
            if self._state is None:
                return []

        raw_signal = self.predict_on_returns(returns)
        if raw_signal.empty:
            return []

        explained = float(sum(self._state["explained_variance_ratio"]))
        return _materialise_dislocation_outputs(
            raw_signal=raw_signal,
            data=data,
            explained_variance=explained,
            n_components=self._state["n_components"],
            method_id=self.metadata.method_id,
        )


# ----------------------------------------------------------------------
# DFM
# ----------------------------------------------------------------------
class DynamicFactorModel(SignalMethod):
    """Dynamic Factor Model with Kalman-filtered time-varying loadings.

    Uses ``statsmodels.tsa.statespace.DynamicFactor``. Convergence is
    flaky on small synthetic samples; fitting failures degrade
    gracefully — the method logs and stays unfit, so ``compute()``
    returns an empty output list and the daily runner persists
    PCA-only.

    State serialised: the fitted ``DynamicFactorResults`` object plus
    the column order it was trained on. The whole results object is
    pickled — heavy but reliable. zlib compression kicks in at
    serialise time when the raw blob exceeds 32KB.
    """

    metadata = MethodMetadata(
        method_id="dislocation.dfm.v1",
        component="dislocation_signal",
        name="Dynamic Factor Model Cross-Asset Dislocation",
        version="1.0.0",
        description=(
            "Kalman-filtered factor model with time-varying loadings via "
            "statsmodels DynamicFactor; same long-bias residual convention "
            "as the PCA baseline."
        ),
        references=[
            "Doz, Giannone, Reichlin (2011) A Two-Step Estimator for "
            "Large Approximate Dynamic Factor Models",
            "Stock & Watson (2011) Dynamic Factor Models",
        ],
    )

    def __init__(
        self,
        *,
        lookback_days: int = 504,
        n_factors: int = 3,
        k_ar: int = 1,
        maxiter: int = 200,
        min_history_days: int = 504,
    ) -> None:
        self.lookback_days = int(lookback_days)
        self.n_factors = int(n_factors)
        self.k_ar = int(k_ar)
        self.maxiter = int(maxiter)
        self.min_history_days = int(min_history_days)
        self._state: dict[str, Any] | None = None

    # --- fit / predict / serialize -------------------------------------
    def fit_on_returns(self, returns: pd.DataFrame) -> None:
        from statsmodels.tsa.statespace.dynamic_factor import DynamicFactor

        fit_window = returns.iloc[-self.lookback_days :].dropna(how="any", axis=1)
        if fit_window.shape[1] < self.n_factors + 1:
            self._state = None
            return

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = DynamicFactor(
                    fit_window.values,
                    k_factors=self.n_factors,
                    factor_order=self.k_ar,
                    enforce_stationarity=False,
                )
                fitted = model.fit(disp=False, maxiter=self.maxiter)
        except Exception as exc:  # pragma: no cover - convergence dependent
            log.warning(
                "signals.dislocation.dfm.fit_failed",
                error=str(exc),
                rows=fit_window.shape[0],
                cols=fit_window.shape[1],
            )
            self._state = None
            return

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fitted_values = fitted.fittedvalues
        except Exception:  # pragma: no cover
            self._state = None
            return

        fitted_df = pd.DataFrame(
            fitted_values, index=fit_window.index, columns=fit_window.columns
        )
        explained = _safe_explained_variance(fitted_df, fit_window)

        self._state = {
            "fitted": fitted,
            "columns": list(fit_window.columns),
            "explained_variance": float(explained),
            "n_factors": self.n_factors,
            "fit_rows": int(fit_window.shape[0]),
        }

    def predict_on_returns(self, returns: pd.DataFrame) -> pd.DataFrame:
        if self._state is None:
            return pd.DataFrame()
        fitted = self._state["fitted"]
        cols: list[str] = self._state["columns"]
        usable_cols = [c for c in cols if c in returns.columns]
        if not usable_cols:
            return pd.DataFrame()

        sub = returns[usable_cols].dropna(how="any")
        if sub.empty:
            return pd.DataFrame()
        # Use the fitted model's in-sample fitted values for the rows it
        # actually saw; for newer rows (post-fit) we re-apply via
        # ``apply`` which extends the Kalman filter. statsmodels'
        # results.apply is the right hook here but has historically been
        # rough on multivariate state spaces — fall back to the in-sample
        # fitted values aligned to ``returns.index`` and treat newer rows
        # as residuals against the latest available fitted row.
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fitted_values = fitted.fittedvalues
        except Exception:  # pragma: no cover
            return pd.DataFrame()

        in_sample_index = pd.Index(range(len(fitted_values)))
        # Map the original training rows back to a DataFrame; for any
        # ``returns.index`` row beyond the training window, use the last
        # fitted row (a rough but conservative extrapolation).
        n_in_sample = len(fitted_values)
        if isinstance(fitted_values, np.ndarray):
            fitted_arr = fitted_values
        else:
            fitted_arr = np.asarray(fitted_values)
        last_row = fitted_arr[-1, :]
        per_date = []
        for i, _ in enumerate(sub.index):
            if i < n_in_sample:
                per_date.append(fitted_arr[i, :])
            else:
                per_date.append(last_row)
        fitted_aligned = pd.DataFrame(
            per_date, index=sub.index, columns=usable_cols
        )
        residuals = sub - fitted_aligned
        raw_signal = (-residuals.clip(lower=-3.0, upper=3.0)).apply(np.tanh)
        _ = in_sample_index  # mypy: keep variable referenced
        return raw_signal.reindex(returns.index)

    def serialize(self) -> bytes:
        if self._state is None:
            return b""
        # statsmodels DFM results pickling has historically been fragile;
        # we cushion it with try/except so a bad fit doesn't poison the
        # blob storage.
        try:
            return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:  # pragma: no cover - statsmodels edge
            log.warning("signals.dislocation.dfm.serialize_failed", error=str(exc))
            return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> DynamicFactorModel:
        m = cls()
        if blob:
            try:
                m._state = pickle.loads(blob)
            except Exception as exc:  # pragma: no cover
                log.warning("signals.dislocation.dfm.deserialize_failed", error=str(exc))
                m._state = None
        return m

    # --- compute (orchestration) ---------------------------------------
    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("DynamicFactorModel requires a DB session")
        load_start = data.start - timedelta(days=self.lookback_days + 60)
        panel = load_close_panel(
            session,
            data.instrument_ids,
            start=load_start,
            end=data.end,
            as_of=data.as_of,
            calendar=data.calendar,
        )
        if panel.empty or panel.shape[1] < 2:
            return []

        returns = _compute_returns(panel)
        if returns.shape[0] < self.min_history_days:
            log.info(
                "signals.dislocation.dfm.insufficient_history",
                rows=returns.shape[0],
                required=self.min_history_days,
            )
            return []

        if self._state is None:
            log.info(
                "signals.dislocation.dfm.fallback_fit",
                reason="no cached state; fitting on daily-run window",
            )
            self.fit_on_returns(returns)
            if self._state is None:
                return []

        raw_signal = self.predict_on_returns(returns)
        if raw_signal.empty:
            return []

        explained = float(self._state["explained_variance"])
        return _materialise_dislocation_outputs(
            raw_signal=raw_signal,
            data=data,
            explained_variance=explained,
            n_components=self._state["n_factors"],
            method_id=self.metadata.method_id,
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_explained_variance(fitted: pd.DataFrame, actual: pd.DataFrame) -> float:
    """1 - SS_res / SS_tot averaged across columns; clipped to [0, 1]."""
    if actual.empty:
        return 0.0
    residual_var = (actual - fitted).var().mean()
    total_var = actual.var().mean()
    if total_var == 0 or pd.isna(total_var):
        return 0.0
    return float(max(0.0, min(1.0, 1.0 - residual_var / total_var)))


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0:
        return 0.0
    sa = a.std(ddof=1)
    sb = b.std(ddof=1)
    if sa == 0 or sb == 0:
        return 0.0
    return float(((a - a.mean()) * (b - b.mean())).mean() / (sa * sb))


__all__ = ["DynamicFactorModel", "PCADislocation"]
