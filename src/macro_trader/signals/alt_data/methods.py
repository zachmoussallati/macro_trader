"""Alt-data signal methods.

Each method is a stateless transform of already-ingested Stage 2
data into a per-instrument z-scored signal value with the project-
wide long-bias convention (positive raw_value = long).

No fit / refit: alt-data signals are pure functions of the latest
data slice. ``serialize`` returns ``b""`` and ``deserialize`` returns
a fresh instance. The weekly-refit Dagster asset path used by the
factor-exposure / catalyst / dislocation families is intentionally
not wired here.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from sqlalchemy import select

from macro_trader.db.models.alt_data import (
    EIAInventory,
    GoogleTrends,
    USDAReport,
)
from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.output import cross_sectional_rank

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------
def _zscore(values: pd.Series, *, window: int, min_periods: int = 20) -> pd.Series:
    if values.empty:
        return values
    mu = values.rolling(window=window, min_periods=min_periods).mean()
    sigma = values.rolling(window=window, min_periods=min_periods).std(ddof=1).replace(
        0, np.nan
    )
    return (values - mu) / sigma


def _materialise_alt_outputs(
    *,
    instrument_signal: dict[str, float],
    confidence_per_instrument: dict[str, float],
    data: SignalInput,
    method_id: str,
    extras: dict[str, Any] | None = None,
) -> list[SignalOutput]:
    if not instrument_signal:
        return []
    ranks = cross_sectional_rank(
        {k: abs(v) for k, v in instrument_signal.items() if v != 0.0}
    )
    outputs: list[SignalOutput] = []
    for inst, score in instrument_signal.items():
        meta: dict[str, Any] = {"method_id": method_id}
        if extras and inst in extras:
            meta.update(extras[inst])
        outputs.append(
            SignalOutput(
                instrument_id=inst,
                value_ts=data.as_of,
                observation_ts=data.as_of,
                raw_value=float(np.tanh(score)),
                zscore=float(score),
                rank=float(ranks.get(inst, 0.5)),
                confidence=float(confidence_per_instrument.get(inst, 0.05)),
                rolling_sharpe_252=None,
                metadata=meta,
            )
        )
    return outputs


# ----------------------------------------------------------------------
# EIA Weekly Storage Surprise
# ----------------------------------------------------------------------
# Map FRED-like EIA series_ids → which instruments the surprise affects.
_EIA_SERIES_TO_INSTRUMENTS: dict[str, tuple[str, ...]] = {
    "PET.WCRSTUS1.W": ("CL", "BZ"),       # commercial crude stocks
    "PET.WGTSTUS1.W": ("RB",),            # gasoline stocks
    "PET.WDISTUS1.W": ("HO",),            # distillate stocks
    "NG.NW2_EPG0_SWO_R48_BCF.W": ("NG",),  # working gas in storage
}


class EIAStorageSurprise(SignalMethod):
    """Weekly storage surprise vs 5-year seasonal average.

    For each EIA series, compute the latest value's deviation from
    its same-week-of-year average over the trailing 5 years, then
    z-score over rolling 156 weeks. Sign-invert so positive supply
    surprise (more inventory than seasonal) is bearish.
    """

    metadata = MethodMetadata(
        method_id="alt_data.eia_storage.v1",
        component="alt_data_signal",
        name="EIA Storage Seasonal Surprise",
        version="1.0.0",
        description="Weekly storage surprise vs 5-year seasonal pattern.",
        references=[],
    )

    def __init__(
        self,
        *,
        seasonal_window_years: int = 5,
        zscore_window_weeks: int = 156,
    ) -> None:
        self.seasonal_window_years = int(seasonal_window_years)
        self.zscore_window_weeks = int(zscore_window_weeks)

    def fit(self, data: SignalInput) -> None:
        return None

    def predict(self, data: SignalInput) -> list[SignalOutput]:
        session = data.extras.get("session")
        return self.compute(data, session)

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> EIAStorageSurprise:
        return cls()

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("EIAStorageSurprise requires a DB session")
        cutoff = data.as_of - timedelta(weeks=self.zscore_window_weeks)
        per_instrument_score: dict[str, float] = {}
        per_instrument_conf: dict[str, float] = {}
        per_instrument_meta: dict[str, dict[str, Any]] = {}

        for series_id, instruments in _EIA_SERIES_TO_INSTRUMENTS.items():
            rows = list(
                session.scalars(
                    select(EIAInventory)
                    .where(EIAInventory.series_id == series_id)
                    .where(EIAInventory.value_ts >= cutoff)
                    .where(EIAInventory.observation_ts <= data.as_of)
                    .order_by(EIAInventory.value_ts.asc())
                )
            )
            if not rows:
                continue
            df = pd.DataFrame(
                {
                    "value_ts": [pd.Timestamp(r.value_ts) for r in rows],
                    "value": [
                        float(r.value) if r.value is not None else np.nan for r in rows
                    ],
                }
            ).dropna()
            if df.empty:
                continue
            df["woy"] = df["value_ts"].dt.isocalendar().week
            df["year"] = df["value_ts"].dt.year
            df = df.sort_values("value_ts").reset_index(drop=True)

            # 5-year seasonal average for the most recent week.
            latest = df.iloc[-1]
            seasonal_mask = (
                (df["woy"] == latest["woy"])
                & (df["year"] < latest["year"])
                & (df["year"] >= latest["year"] - self.seasonal_window_years)
            )
            seasonal_pool = df.loc[seasonal_mask, "value"]
            if seasonal_pool.empty:
                continue
            surprise = float(latest["value"] - seasonal_pool.mean())

            # Z-score the surprise over historical weekly surprises
            # for the same series.
            historical_surprise: list[float] = []
            for _, row in df.iterrows():
                pool = df[
                    (df["woy"] == row["woy"])
                    & (df["year"] < row["year"])
                    & (df["year"] >= row["year"] - self.seasonal_window_years)
                ]
                if pool.empty:
                    continue
                historical_surprise.append(row["value"] - pool["value"].mean())

            if not historical_surprise:
                continue
            hs = pd.Series(historical_surprise)
            mu = hs.mean()
            sigma = hs.std(ddof=1) or 1.0
            z = (surprise - mu) / sigma
            # Long-bias convention: high supply (positive surprise) =>
            # bearish => negative raw_value via negation.
            score = float(-np.clip(z, -3.0, 3.0))

            history_weeks = min(len(df), self.zscore_window_weeks)
            conf = max(0.05, min(1.0, history_weeks / float(self.zscore_window_weeks)))

            for inst in instruments:
                per_instrument_score[inst] = score
                per_instrument_conf[inst] = conf
                per_instrument_meta[inst] = {
                    "series_id": series_id,
                    "surprise": surprise,
                    "z": z,
                    "weeks_of_history": int(history_weeks),
                }

        # Instruments not covered by any series: emit zero / no-data.
        for inst in data.instrument_ids:
            if inst not in per_instrument_score:
                per_instrument_score[inst] = 0.0
                per_instrument_conf[inst] = 0.0
                per_instrument_meta[inst] = {"covered": False}

        return _materialise_alt_outputs(
            instrument_signal=per_instrument_score,
            confidence_per_instrument=per_instrument_conf,
            data=data,
            method_id=self.metadata.method_id,
            extras=per_instrument_meta,
        )


# ----------------------------------------------------------------------
# USDA WASDE Surprise
# ----------------------------------------------------------------------
_USDA_INSTRUMENTS: tuple[str, ...] = ("ZC", "ZS", "ZW")


class USDAWASDESurprise(SignalMethod):
    """Monthly WASDE month-over-month change in production / yield.

    Stage 2's USDA ingester populates ``alt_data.usda_reports`` with
    (commodity, metric) rows including ``production`` and ``yield``.
    We compute the MoM change in production and z-score over rolling
    36 reports (3 years). Sign-invert so positive supply MoM
    (more bushels) is bearish.
    """

    metadata = MethodMetadata(
        method_id="alt_data.usda_wasde.v1",
        component="alt_data_signal",
        name="USDA WASDE Production Surprise",
        version="1.0.0",
        description=(
            "Monthly WASDE production MoM change z-scored over 36 reports."
        ),
        references=[],
    )

    def __init__(self, *, zscore_window_reports: int = 36) -> None:
        self.zscore_window_reports = int(zscore_window_reports)

    def fit(self, data: SignalInput) -> None:
        return None

    def predict(self, data: SignalInput) -> list[SignalOutput]:
        session = data.extras.get("session")
        return self.compute(data, session)

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> USDAWASDESurprise:
        return cls()

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("USDAWASDESurprise requires a DB session")
        per_instrument_score: dict[str, float] = {}
        per_instrument_conf: dict[str, float] = {}
        per_instrument_meta: dict[str, dict[str, Any]] = {}

        # USDA commodity strings match Stage 2's ingester output
        # (corn / soybeans / wheat).
        commodity_for_instrument = {"ZC": "corn", "ZS": "soybeans", "ZW": "wheat"}

        for inst, commodity in commodity_for_instrument.items():
            rows = list(
                session.scalars(
                    select(USDAReport)
                    .where(USDAReport.commodity == commodity)
                    .where(USDAReport.metric == "production")
                    .where(USDAReport.observation_ts <= data.as_of)
                    .order_by(USDAReport.value_ts.asc())
                )
            )
            if len(rows) < 3:
                continue
            values = pd.Series(
                [float(r.value) for r in rows if r.value is not None]
            )
            if values.empty:
                continue
            mom = values.pct_change().dropna()
            if mom.empty:
                continue
            mom_z = _zscore(mom, window=self.zscore_window_reports, min_periods=10)
            latest_z = mom_z.dropna()
            if latest_z.empty:
                continue
            z = float(np.clip(latest_z.iloc[-1], -3.0, 3.0))
            # More production = bearish for the grain.
            score = -z
            history = len(values)
            conf = max(0.05, min(1.0, history / float(self.zscore_window_reports)))
            per_instrument_score[inst] = score
            per_instrument_conf[inst] = conf
            per_instrument_meta[inst] = {
                "commodity": commodity,
                "z": z,
                "n_reports": int(history),
            }

        # Instruments outside the grains universe: not covered.
        for inst in data.instrument_ids:
            if inst not in per_instrument_score:
                per_instrument_score[inst] = 0.0
                per_instrument_conf[inst] = 0.0
                per_instrument_meta[inst] = {"covered": False}

        return _materialise_alt_outputs(
            instrument_signal=per_instrument_score,
            confidence_per_instrument=per_instrument_conf,
            data=data,
            method_id=self.metadata.method_id,
            extras=per_instrument_meta,
        )


# ----------------------------------------------------------------------
# Google Trends Sentiment Composite
# ----------------------------------------------------------------------
_DEFAULT_TRENDS_QUERY_MAP: dict[str, tuple[str, ...]] = {
    "oil price": ("CL", "BZ"),
    "gold": ("GC",),
    "copper price": ("HG",),
    "buy gold": ("GC",),
    "natural gas": ("NG",),
}


class GoogleTrendsSentiment(SignalMethod):
    """Contrarian Google Trends sentiment.

    For each query in the configured mapping, pull recent interest
    values, smooth with a 7-day EWM, z-score over rolling 90 days.
    High recent interest is taken as a contrarian short signal.
    """

    metadata = MethodMetadata(
        method_id="alt_data.google_trends.v1",
        component="alt_data_signal",
        name="Google Trends Sentiment Composite",
        version="1.0.0",
        description=(
            "Smoothed Google Trends interest z-scored over 90 days, "
            "inverted for contrarian sentiment."
        ),
        references=[],
    )

    def __init__(
        self,
        *,
        smoothing_window_days: int = 7,
        zscore_window_days: int = 90,
        query_mapping: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.smoothing_window_days = int(smoothing_window_days)
        self.zscore_window_days = int(zscore_window_days)
        self.query_mapping = query_mapping or _DEFAULT_TRENDS_QUERY_MAP

    def fit(self, data: SignalInput) -> None:
        return None

    def predict(self, data: SignalInput) -> list[SignalOutput]:
        session = data.extras.get("session")
        return self.compute(data, session)

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> GoogleTrendsSentiment:
        return cls()

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("GoogleTrendsSentiment requires a DB session")
        cutoff = data.as_of - timedelta(days=self.zscore_window_days * 2)

        # Aggregate per-instrument by averaging across that
        # instrument's relevant queries.
        per_instrument_zs: dict[str, list[float]] = {}
        per_instrument_meta: dict[str, dict[str, Any]] = {}
        per_instrument_conf: dict[str, float] = {}

        for query, instruments in self.query_mapping.items():
            rows = list(
                session.scalars(
                    select(GoogleTrends)
                    .where(GoogleTrends.query_term == query)
                    .where(GoogleTrends.value_ts >= cutoff)
                    .where(GoogleTrends.observation_ts <= data.as_of)
                    .order_by(GoogleTrends.value_ts.asc())
                )
            )
            if not rows:
                continue
            s = pd.Series(
                [float(r.value) if r.value is not None else np.nan for r in rows],
                index=pd.to_datetime([r.value_ts for r in rows], utc=True),
            ).dropna()
            if s.empty:
                continue
            smoothed = s.ewm(span=self.smoothing_window_days, adjust=False).mean()
            z_series = _zscore(
                smoothed,
                window=self.zscore_window_days,
                min_periods=max(15, self.zscore_window_days // 3),
            ).dropna()
            if z_series.empty:
                continue
            z = float(np.clip(z_series.iloc[-1], -3.0, 3.0))
            conf = max(
                0.05,
                min(1.0, len(smoothed) / float(self.zscore_window_days)),
            )
            for inst in instruments:
                per_instrument_zs.setdefault(inst, []).append(z)
                per_instrument_meta.setdefault(inst, {}).setdefault(
                    "queries", []
                ).append(query)
                per_instrument_conf[inst] = max(per_instrument_conf.get(inst, 0.0), conf)

        per_instrument_score: dict[str, float] = {}
        for inst in data.instrument_ids:
            zs = per_instrument_zs.get(inst)
            if not zs:
                per_instrument_score[inst] = 0.0
                per_instrument_conf[inst] = 0.0
                per_instrument_meta[inst] = {"covered": False}
                continue
            # Contrarian: invert.
            avg_z = float(np.mean(zs))
            per_instrument_score[inst] = -avg_z

        return _materialise_alt_outputs(
            instrument_signal=per_instrument_score,
            confidence_per_instrument=per_instrument_conf,
            data=data,
            method_id=self.metadata.method_id,
            extras=per_instrument_meta,
        )


__all__ = ["EIAStorageSurprise", "GoogleTrendsSentiment", "USDAWASDESurprise"]
_ = datetime  # silence unused
