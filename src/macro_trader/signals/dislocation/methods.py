"""Cross-asset dislocation signal methods.

Both methods share the same conceptual signal: fit a factor model on
the panel of instrument returns and emit the *residual* (the part of an
instrument's return that the factor model couldn't explain) as a
mean-reversion signal. Positive residual = instrument outperformed the
peer prediction = contrarian short tilt = negative ``raw_value`` (per
the project-wide long-bias convention).

Stage 4A intentionally keeps both fitting strategies simple:

- PCA refits on each ``compute()`` call (fast for 13 instruments x 252
  days). Weekly refit + serialised model storage in
  ``system.methods_registry.serialized_blob`` is the natural follow-up
  enhancement once we have a job to drive it; the current code path
  is correct in both regimes — see ``notes/stage_4a/tradeoffs.md``.
- DFM uses ``statsmodels.tsa.statespace.DynamicFactor``. Convergence on
  small synthetic samples is flaky; the implementation catches the
  failure and returns empty outputs (logged) rather than propagating
  upstream — degrading to "PCA-only this run" is preferable to crashing
  the whole signals job.
"""

from __future__ import annotations

import warnings
from datetime import timedelta
from typing import TYPE_CHECKING

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
            # Confidence: scale by explained variance for PCA / DFM so
            # weak factor structure downweights the signal.
            conf = min(1.0, explained_variance)
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
                    metadata={
                        "method_id": method_id,
                        "n_components": n_components,
                        "explained_variance": float(explained_variance),
                    },
                )
            )
    return outputs


class PCADislocation(SignalMethod):
    """PCA-based cross-asset dislocation signal.

    Workflow:

    1. Load a return panel covering the lookback + emit window.
    2. Fit PCA on the lookback window; keep ``n_components`` factors.
    3. For each row in the emit window, reconstruct the row from the
       top-K factors and compute the residual.
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

        fit_window = returns.iloc[-self.lookback_days :].dropna(how="any", axis=1)
        if fit_window.shape[1] < self.n_components + 1:
            log.info(
                "signals.dislocation.pca.too_few_instruments",
                kept=fit_window.shape[1],
                required=self.n_components + 1,
            )
            return []

        pca = PCA(n_components=self.n_components, svd_solver="full")
        pca.fit(fit_window.values)

        # Reconstruct returns from top-K factors on the *full* panel.
        usable_cols = fit_window.columns
        recon_returns = returns[usable_cols].copy()
        scores = pca.transform(returns[usable_cols].dropna(how="any").values)
        reconstructed = pca.inverse_transform(scores)
        recon_returns = recon_returns.dropna(how="any")
        recon_returns.loc[:, :] = reconstructed
        residuals = returns[usable_cols].dropna(how="any") - recon_returns

        # Long-bias convention: positive residual = outperformed peers =
        # contrarian short = negative raw_value.
        raw_signal = (-residuals.clip(lower=-3.0, upper=3.0)).apply(np.tanh)
        # Reindex onto the calendar.
        raw_signal = raw_signal.reindex(returns.index)

        explained = float(pca.explained_variance_ratio_.sum())
        return _materialise_dislocation_outputs(
            raw_signal=raw_signal,
            data=data,
            explained_variance=explained,
            n_components=self.n_components,
            method_id=self.metadata.method_id,
        )


class DynamicFactorModel(SignalMethod):
    """Dynamic Factor Model with Kalman-filtered time-varying loadings.

    Uses ``statsmodels.tsa.statespace.DynamicFactor`` (the standard
    state-space DFM). Convergence is flaky on small synthetic samples;
    when it fails the method logs and returns an empty output list — the
    pipeline's behaviour gracefully degrades to "PCA only this run".
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

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("DynamicFactorModel requires a DB session")
        from statsmodels.tsa.statespace.dynamic_factor import DynamicFactor

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

        fit_window = returns.iloc[-self.lookback_days :].dropna(how="any", axis=1)
        if fit_window.shape[1] < self.n_factors + 1:
            return []

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
        except Exception as exc:  # pragma: no cover - convergence-dependent
            log.warning(
                "signals.dislocation.dfm.fit_failed",
                error=str(exc),
                rows=fit_window.shape[0],
                cols=fit_window.shape[1],
            )
            return []

        # Fitted values + residuals on the fit window.
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fitted_values = fitted.fittedvalues
        except Exception:  # pragma: no cover - statsmodels edge case
            return []

        fitted_df = pd.DataFrame(
            fitted_values, index=fit_window.index, columns=fit_window.columns
        )
        residuals = fit_window - fitted_df

        raw_signal = (-residuals.clip(lower=-3.0, upper=3.0)).apply(np.tanh)
        raw_signal = raw_signal.reindex(returns.index)

        explained = _safe_explained_variance(fitted_df, fit_window)
        return _materialise_dislocation_outputs(
            raw_signal=raw_signal,
            data=data,
            explained_variance=explained,
            n_components=self.n_factors,
            method_id=self.metadata.method_id,
        )


def _safe_explained_variance(fitted: pd.DataFrame, actual: pd.DataFrame) -> float:
    """1 - SS_res / SS_tot averaged across columns; clipped to [0, 1]."""
    if actual.empty:
        return 0.0
    residual_var = (actual - fitted).var().mean()
    total_var = actual.var().mean()
    if total_var == 0 or pd.isna(total_var):
        return 0.0
    return float(max(0.0, min(1.0, 1.0 - residual_var / total_var)))


__all__ = ["DynamicFactorModel", "PCADislocation"]
