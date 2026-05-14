"""Signal framework primitives.

A signal method consumes a :class:`SignalInput` (universe + time window +
``as_of`` anchor) and returns a list of :class:`SignalOutput` rows — one
per (instrument, value_ts) the method computed for. The runner takes care
of persisting these to ``signals.signal_values`` and running the family
comparator.

Signals are stateless by design — ``fit`` is a no-op, ``predict`` defers to
``compute`` which takes a fresh DB session each run. Cross-run state lives
in the DB (vintaged data) or in the registry (fitted blobs, for methods
that need them; none in Stage 3).
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from macro_trader.methods.base import Method, MethodMetadata
from macro_trader.methods.comparator import MethodComparator

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class SignalInput:
    """Universe + time window for a signal run.

    ``as_of`` is the point-in-time anchor: any query the signal performs
    must filter on ``observation_ts <= as_of`` (market data) or the
    equivalent vintage check (macro data). Tests pin ``as_of`` to make
    runs deterministic.
    """

    instrument_ids: list[str]
    as_of: datetime
    start: datetime
    end: datetime
    calendar: str = "NYSE"
    # Optional regime hint — wired but unused in Stage 3, activated in
    # Stage 6 when the regime classifier comes online.
    regime_state: str | None = None
    # Free-form params subclasses may pass through (e.g. ensemble weights).
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalOutput:
    """One row of signal output. Maps 1:1 to ``signals.signal_values``."""

    instrument_id: str
    value_ts: datetime
    observation_ts: datetime
    raw_value: float
    zscore: float
    rank: float
    confidence: float
    rolling_sharpe_252: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SignalMethod(Method[SignalInput, list[SignalOutput]]):
    """All signal methods inherit from this.

    Subclasses implement :meth:`compute`. The :meth:`predict` API on the
    parent ``Method`` redirects to ``compute`` so signal methods slot into
    the standard methods-framework comparator + registry plumbing.
    """

    metadata: MethodMetadata

    # --- Method interface -------------------------------------------------
    def fit(self, data: SignalInput) -> None:
        return None

    def predict(self, data: SignalInput) -> list[SignalOutput]:
        # The comparator drives predict() directly with a SignalInput; some
        # comparators may want to bypass the DB and pass a session via
        # `data.extras["session"]`. Most callers go through `compute`.
        session = data.extras.get("session")
        return self.compute(data, session)

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> SignalMethod:
        return cls()

    # --- Subclass hook ----------------------------------------------------
    @abstractmethod
    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        """Return per-instrument signal rows for the requested window.

        ``session`` may be ``None`` only when the caller has already loaded
        every input the signal needs and passed it via ``data.extras``.
        Most signals load data through :mod:`macro_trader.data.loaders`
        and require a session.
        """


# ----------------------------------------------------------------------
# Family comparator base
# ----------------------------------------------------------------------
class SignalFamilyComparator(MethodComparator[SignalInput, list[SignalOutput]]):
    """Common comparator for signal families.

    Subclasses set ``component`` and optionally override
    :meth:`_extra_metrics` to add per-family metrics. The base implementation
    measures direction agreement, rank correlation, value correlation,
    rolling Sharpe (both methods), turnover, stability, and confidence —
    enough to drive Stage 7 composite weighting without a backtester yet.
    """

    def _compute_metrics(
        self,
        output_a: list[SignalOutput],
        output_b: list[SignalOutput],
        *,
        data: SignalInput,
    ) -> dict[str, float]:
        df_a = _outputs_to_dataframe(output_a)
        df_b = _outputs_to_dataframe(output_b)
        if df_a.empty or df_b.empty:
            return {"n_observations_a": float(len(df_a)), "n_observations_b": float(len(df_b))}

        # Align on (instrument, value_ts) so we compare apples to apples.
        merged = df_a.join(df_b, lsuffix="_a", rsuffix="_b", how="inner")
        n = len(merged)
        if n == 0:
            return {"n_observations_a": float(len(df_a)), "n_observations_b": float(len(df_b))}

        sign_a = np.sign(merged["raw_value_a"].fillna(0.0))
        sign_b = np.sign(merged["raw_value_b"].fillna(0.0))
        direction_agreement = float((sign_a == sign_b).mean())

        rank_corr = _safe_correlation(merged["rank_a"], merged["rank_b"], method="spearman")
        value_corr = _safe_correlation(merged["raw_value_a"], merged["raw_value_b"])

        turnover_a = _mean_change(merged["raw_value_a"])
        turnover_b = _mean_change(merged["raw_value_b"])

        metrics = {
            "n_observations": float(n),
            "direction_agreement": direction_agreement,
            "rank_correlation_a_b": rank_corr,
            "value_correlation_a_b": value_corr,
            "rolling_sharpe_a": float(merged["rolling_sharpe_252_a"].dropna().mean())
            if "rolling_sharpe_252_a" in merged
            else 0.0,
            "rolling_sharpe_b": float(merged["rolling_sharpe_252_b"].dropna().mean())
            if "rolling_sharpe_252_b" in merged
            else 0.0,
            "stability_a": 1.0 - turnover_a,
            "stability_b": 1.0 - turnover_b,
            "turnover_a": turnover_a,
            "turnover_b": turnover_b,
            "confidence_a": float(merged["confidence_a"].mean()),
            "confidence_b": float(merged["confidence_b"].mean()),
        }
        metrics.update(self._extra_metrics(merged))
        return metrics

    def _extra_metrics(self, merged: pd.DataFrame) -> dict[str, float]:
        """Hook for family-specific metrics. Default = no extras."""
        return {}


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _outputs_to_dataframe(outputs: list[SignalOutput]) -> pd.DataFrame:
    if not outputs:
        return pd.DataFrame()
    rows = [
        {
            "instrument_id": o.instrument_id,
            "value_ts": pd.Timestamp(o.value_ts),
            "raw_value": o.raw_value,
            "zscore": o.zscore,
            "rank": o.rank,
            "confidence": o.confidence,
            "rolling_sharpe_252": o.rolling_sharpe_252,
        }
        for o in outputs
    ]
    return pd.DataFrame(rows).set_index(["instrument_id", "value_ts"])


def _safe_correlation(a: pd.Series, b: pd.Series, *, method: str = "pearson") -> float:
    """Pearson/Spearman corr that returns 0 instead of NaN for degenerate input."""
    if a.empty or b.empty:
        return 0.0
    if a.nunique() <= 1 or b.nunique() <= 1:
        return 0.0
    val = a.corr(b, method=method)
    if val is None or pd.isna(val):
        return 0.0
    return float(val)


def _mean_change(series: pd.Series, *, threshold: float = 0.1) -> float:
    """Mean fraction of consecutive observations that change by more than `threshold`."""
    if series.empty:
        return 0.0
    diffs = series.groupby(level="instrument_id").diff().abs()
    big = (diffs > threshold).fillna(False)
    return float(big.mean())
