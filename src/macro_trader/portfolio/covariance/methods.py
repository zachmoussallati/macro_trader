"""Two covariance methods.

- ``covariance.ledoit_wolf.v1`` (BASELINE) — sklearn LedoitWolf
  shrinkage; stateless; recomputed daily.
- ``covariance.dcc_garch.v1`` (SHADOW, gated on ``arch``) — per-
  instrument GARCH(1, 1) for vol + Dynamic Conditional Correlation
  for cross-instrument correlations.

Both share the :class:`CovarianceInput` / :class:`CovarianceOutput`
shapes and the same returns-panel loader. Only the *estimation*
step differs.
"""

# Stat-ML convention uses uppercase R / Q / D — suppress N806
# file-wide so numpy code stays readable.
# ruff: noqa: N806

from __future__ import annotations

import pickle
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Self

import numpy as np
import pandas as pd
from sqlalchemy import select

from macro_trader.db.models.market_data import DailyBar
from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import Method, MethodMetadata

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def _arch_available() -> bool:
    try:
        import arch  # noqa: F401
    except Exception:
        return False
    return True


# ----------------------------------------------------------------------
# Input / output shapes
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class CovarianceInput:
    instrument_ids: list[str]
    as_of: datetime
    lookback_days: int = 252


@dataclass(frozen=True)
class CovarianceOutput:
    """Result of a single covariance run.

    Always carries both the pair-keyed dict (sparse-friendly DB
    persistence) and the dense numpy view (numerical use). The two
    are equivalent; downstream consumers pick whichever fits.
    """

    as_of: datetime
    instrument_ids: list[str]
    covariance_matrix: np.ndarray  # (n, n), symmetric
    correlation_matrix: np.ndarray  # (n, n), symmetric, diag = 1
    annualised_vols: np.ndarray    # (n,), annualised stdev
    lookback_days: int
    metadata: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Shared loader
# ----------------------------------------------------------------------
def load_returns_panel(
    session: Session,
    *,
    instrument_ids: list[str],
    as_of: datetime,
    lookback_days: int,
) -> pd.DataFrame:
    """Build the returns panel for the covariance estimator.

    Returns a DataFrame indexed by value_ts with one column per
    instrument. Cells are log-returns; the first row is dropped
    (no prior close to diff against).
    """
    if not instrument_ids:
        return pd.DataFrame()
    start = as_of - timedelta(days=lookback_days + 30)
    rows = list(
        session.scalars(
            select(DailyBar)
            .where(DailyBar.instrument_id.in_(instrument_ids))
            .where(DailyBar.value_ts >= start)
            .where(DailyBar.value_ts <= as_of)
        )
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        [
            {
                "value_ts": r.value_ts,
                "instrument_id": r.instrument_id,
                "close": float(r.close) if r.close is not None else None,
            }
            for r in rows
        ]
    ).dropna()
    wide = df.pivot_table(
        index="value_ts", columns="instrument_id", values="close", aggfunc="last"
    ).sort_index()
    rets = np.log(wide / wide.shift(1)).dropna(how="all")
    return rets


# ----------------------------------------------------------------------
# Base class
# ----------------------------------------------------------------------
class CovarianceMethod(Method[CovarianceInput, CovarianceOutput]):
    """Common scaffolding."""

    metadata: MethodMetadata

    def fit(self, data: CovarianceInput) -> None:
        return None

    def predict(self, data: CovarianceInput) -> CovarianceOutput:
        raise RuntimeError("use compute(data, session)")

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> Self:
        return cls()


# ----------------------------------------------------------------------
# Method 1: Ledoit-Wolf shrinkage (BASELINE)
# ----------------------------------------------------------------------
class LedoitWolfCovariance(CovarianceMethod):
    metadata = MethodMetadata(
        method_id="covariance.ledoit_wolf.v1",
        component="covariance_estimate",
        name="Ledoit-Wolf Shrinkage",
        version="1.0.0",
        description=(
            "Sample covariance with optimal shrinkage toward a "
            "structured target (constant correlation). Reduces "
            "estimation error in small samples. The institutional "
            "baseline."
        ),
        references=[
            "Ledoit & Wolf (2004) Honey, I Shrunk the Sample Covariance Matrix"
        ],
    )

    def __init__(
        self,
        *,
        lookback_days: int = 252,
        min_observations: int = 60,
    ) -> None:
        self.lookback_days = int(lookback_days)
        self.min_observations = int(min_observations)

    def compute(
        self, data: CovarianceInput, session: Session | None
    ) -> CovarianceOutput | None:
        if session is None:
            raise ValueError("LedoitWolfCovariance requires a DB session")
        from sklearn.covariance import LedoitWolf

        rets = load_returns_panel(
            session,
            instrument_ids=data.instrument_ids,
            as_of=data.as_of,
            lookback_days=self.lookback_days,
        )
        if rets.empty:
            return None
        rets = rets[data.instrument_ids].dropna(how="any")
        if rets.shape[0] < self.min_observations:
            log.info(
                "covariance.ledoit_wolf.insufficient_history",
                rows=int(rets.shape[0]),
                min=self.min_observations,
            )
            return None

        lw = LedoitWolf()
        lw.fit(rets.values)
        cov = np.asarray(lw.covariance_)
        # Annualise variance by 252 trading days.
        ann_cov = cov * 252.0
        vols = np.sqrt(np.diag(ann_cov))
        # Correlation from the daily covariance (annualisation
        # cancels in the ratio).
        daily_vol = np.sqrt(np.diag(cov))
        with np.errstate(invalid="ignore", divide="ignore"):
            corr = cov / np.outer(daily_vol, daily_vol)
        corr = np.clip(np.nan_to_num(corr, nan=0.0), -1.0, 1.0)
        np.fill_diagonal(corr, 1.0)

        return CovarianceOutput(
            as_of=data.as_of,
            instrument_ids=list(data.instrument_ids),
            covariance_matrix=ann_cov,
            correlation_matrix=corr,
            annualised_vols=vols,
            lookback_days=self.lookback_days,
            metadata={
                "method": self.metadata.method_id,
                "shrinkage": float(getattr(lw, "shrinkage_", float("nan"))),
                "n_observations": int(rets.shape[0]),
            },
        )


# ----------------------------------------------------------------------
# Method 2: DCC-GARCH (SHADOW, gated on arch)
# ----------------------------------------------------------------------
class DCCGARCHCovariance(CovarianceMethod):
    metadata = MethodMetadata(
        method_id="covariance.dcc_garch.v1",
        component="covariance_estimate",
        name="DCC-GARCH Covariance",
        version="1.0.0",
        description=(
            "Per-instrument GARCH(1, 1) volatilities + Dynamic "
            "Conditional Correlation across pairs. Captures regime-"
            "dependent correlation structure that Ledoit-Wolf misses "
            "(correlations spike during crises)."
        ),
        references=[
            "Engle (2002) Dynamic Conditional Correlation",
            "Tsay (2014) Multivariate Time Series Analysis",
        ],
    )

    def __init__(
        self,
        *,
        lookback_days: int = 504,
        min_observations: int = 120,
        dcc_alpha_init: float = 0.05,
        dcc_beta_init: float = 0.93,
        garch_p: int = 1,
        garch_q: int = 1,
    ) -> None:
        if not _arch_available():
            raise RuntimeError(
                "DCCGARCHCovariance requires arch; install via `uv sync --extra ml`"
            )
        self.lookback_days = int(lookback_days)
        self.min_observations = int(min_observations)
        self.dcc_alpha_init = float(dcc_alpha_init)
        self.dcc_beta_init = float(dcc_beta_init)
        self.garch_p = int(garch_p)
        self.garch_q = int(garch_q)
        # Fitted state (the per-instrument GARCH models + the latest
        # DCC parameters). Hydrated by the weekly refit.
        self._state: dict[str, Any] | None = None

    def fit_from_history(
        self,
        *,
        returns_panel: pd.DataFrame,
    ) -> None:
        """Fit per-instrument GARCH + DCC parameters from history."""
        from arch.univariate import arch_model

        rets = returns_panel.dropna(how="any")
        if rets.shape[0] < self.min_observations:
            self._state = None
            return
        instrument_ids = list(rets.columns)
        n = len(instrument_ids)

        garch_models: dict[str, Any] = {}
        std_residuals = np.zeros_like(rets.values)
        cond_vols = np.zeros_like(rets.values)

        for j, inst in enumerate(instrument_ids):
            series = (rets[inst] * 100.0).values  # arch wants returns in pct
            try:
                am = arch_model(
                    series,
                    mean="Zero",
                    vol="GARCH",
                    p=self.garch_p,
                    q=self.garch_q,
                    dist="Normal",
                    rescale=False,
                )
                res = am.fit(disp="off", show_warning=False)
                vol_t = np.asarray(res.conditional_volatility) / 100.0
                eps_t = (rets[inst].values) / np.where(
                    vol_t > 0, vol_t, 1.0
                )
                garch_models[inst] = {
                    "params": res.params.to_dict(),
                    "vol_t": vol_t.tolist(),
                }
                cond_vols[:, j] = vol_t
                std_residuals[:, j] = eps_t
            except Exception as exc:  # pragma: no cover - flaky fits
                log.warning(
                    "covariance.dcc_garch.univariate_failed",
                    instrument=inst,
                    error=str(exc),
                )
                # Fall back to sample std for the failed instrument.
                fallback_vol = float(rets[inst].std(ddof=1)) or 1e-6
                cond_vols[:, j] = fallback_vol
                std_residuals[:, j] = rets[inst].values / fallback_vol
                garch_models[inst] = {"params": None, "vol_t": [fallback_vol] * len(rets)}

        # DCC step: standardised-residual correlation dynamics.
        Q_bar = np.cov(std_residuals.T)
        if not np.all(np.isfinite(Q_bar)):
            Q_bar = np.eye(n)
        Q_t = Q_bar.copy()
        alpha, beta = self.dcc_alpha_init, self.dcc_beta_init
        # Recursive DCC update; final-step Q_t is the matrix that
        # feeds the daily compute step.
        for t in range(rets.shape[0]):
            eps_t = std_residuals[t].reshape(-1, 1)
            Q_t = (
                (1.0 - alpha - beta) * Q_bar
                + alpha * (eps_t @ eps_t.T)
                + beta * Q_t
            )

        self._state = {
            "instrument_ids": instrument_ids,
            "Q_bar": Q_bar,
            "Q_last": Q_t,
            "alpha": float(alpha),
            "beta": float(beta),
            "garch_models": garch_models,
            "last_cond_vols": cond_vols[-1].tolist(),
            "fit_as_of": datetime.utcnow().isoformat(),
            "n_observations": int(rets.shape[0]),
        }

    def compute(
        self, data: CovarianceInput, session: Session | None
    ) -> CovarianceOutput | None:
        if session is None:
            raise ValueError("DCCGARCHCovariance requires a DB session")

        rets = load_returns_panel(
            session,
            instrument_ids=data.instrument_ids,
            as_of=data.as_of,
            lookback_days=self.lookback_days,
        )
        if rets.empty:
            return None
        rets = rets[data.instrument_ids].dropna(how="any")
        if rets.shape[0] < self.min_observations:
            return None

        if self._state is None:
            log.info("covariance.dcc_garch.fallback_fit")
            self.fit_from_history(returns_panel=rets)
            if self._state is None:
                return None

        instrument_ids = list(self._state["instrument_ids"])
        if instrument_ids != data.instrument_ids:
            # Universe changed since fit; re-fit on current panel.
            log.info("covariance.dcc_garch.refit_universe_changed")
            self.fit_from_history(returns_panel=rets)
            if self._state is None:
                return None
            instrument_ids = list(self._state["instrument_ids"])

        Q_t = np.asarray(self._state["Q_last"])
        # Correlation from DCC: R = diag(Q)^{-1/2} * Q * diag(Q)^{-1/2}.
        q_diag = np.sqrt(np.diag(Q_t))
        with np.errstate(divide="ignore", invalid="ignore"):
            R = Q_t / np.outer(q_diag, q_diag)
        R = np.clip(np.nan_to_num(R, nan=0.0), -1.0, 1.0)
        np.fill_diagonal(R, 1.0)

        # Daily covariance = D * R * D where D = diag(cond_vols).
        cond_vols = np.asarray(self._state["last_cond_vols"])
        D = np.diag(cond_vols)
        daily_cov = D @ R @ D
        ann_cov = daily_cov * 252.0
        vols = np.sqrt(np.diag(ann_cov))

        return CovarianceOutput(
            as_of=data.as_of,
            instrument_ids=instrument_ids,
            covariance_matrix=ann_cov,
            correlation_matrix=R,
            annualised_vols=vols,
            lookback_days=self.lookback_days,
            metadata={
                "method": self.metadata.method_id,
                "alpha": float(self._state["alpha"]),
                "beta": float(self._state["beta"]),
                "n_observations": int(self._state.get("n_observations", 0)),
            },
        )

    def serialize(self) -> bytes:
        if self._state is None:
            return b""
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def deserialize(cls, blob: bytes) -> Self:
        if not _arch_available():
            raise RuntimeError(
                "DCCGARCHCovariance cannot deserialize without arch"
            )
        m = cls()
        if blob:
            m._state = pickle.loads(blob)
        return m


__all__ = [
    "CovarianceInput",
    "CovarianceMethod",
    "CovarianceOutput",
    "DCCGARCHCovariance",
    "LedoitWolfCovariance",
    "_arch_available",
    "load_returns_panel",
]


_ = Iterable  # pragma: no cover - reserved for downstream callers
