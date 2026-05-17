"""Regime attribution: per-(regime, signal) historical performance.

For each (regime_method, regime_label, signal_method) triple, compute
over the trailing ``lookback_window_days``:

- ``n_observations`` — count of (date, instrument) pairs in the
  bucket.
- ``mean_return`` — mean of ``sign(signal.raw_value) *
  next_day_log_return``.
- ``sharpe`` — annualised Sharpe of the position-return series.
- ``hit_rate`` — fraction of (date, instrument) pairs where the sign
  of the signal matched the sign of the next-day return.

Stage 7 composite scoring will read this table to weight signals by
regime — "trend ensemble has Sharpe 1.2 in carry_friendly regimes,
0.4 in vol_spike — weight it 3x more in the former".

Run weekly via ``regime_attribution_compute`` Dagster asset.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.db.models.market_data import DailyBar, Instrument
from macro_trader.db.models.regime import RegimeAttribution, RegimeState
from macro_trader.db.models.signals import SignalValue
from macro_trader.logging_setup import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

ANNUALIZATION = 252.0  # trading days per year


def _load_regime_labels(
    session: Session, regime_method_id: str, *, start: datetime, end: datetime
) -> pd.Series:
    """Latest-observation regime label per value_ts for the given method."""
    rows = list(
        session.scalars(
            select(RegimeState)
            .where(RegimeState.method_id == regime_method_id)
            .where(RegimeState.value_ts >= start)
            .where(RegimeState.value_ts <= end)
            .order_by(RegimeState.value_ts, RegimeState.observation_ts)
        )
    )
    if not rows:
        return pd.Series(dtype=object)
    # Keep the latest observation per value_ts.
    by_ts: dict[datetime, str] = {}
    for r in rows:
        by_ts[r.value_ts] = r.label
    return pd.Series(by_ts, name="label").sort_index()


def _load_signal_values(
    session: Session, signal_method_id: str, *, start: datetime, end: datetime
) -> pd.DataFrame:
    """Signal values for one method as (value_ts, instrument_id,
    raw_value)."""
    rows = list(
        session.scalars(
            select(SignalValue)
            .where(SignalValue.signal_id == signal_method_id)
            .where(SignalValue.value_ts >= start)
            .where(SignalValue.value_ts <= end)
        )
    )
    if not rows:
        return pd.DataFrame(columns=["value_ts", "instrument_id", "raw_value"])
    df = pd.DataFrame(
        [
            {
                "value_ts": r.value_ts,
                "instrument_id": r.instrument_id,
                "raw_value": float(r.raw_value)
                if r.raw_value is not None
                else float("nan"),
            }
            for r in rows
        ]
    ).dropna()
    return df


def _load_next_day_returns(
    session: Session,
    instrument_ids: list[str],
    *,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Next-day log-returns indexed by (value_ts, instrument_id)."""
    if not instrument_ids:
        return pd.DataFrame(columns=["value_ts", "instrument_id", "next_log_ret"])
    rows = list(
        session.scalars(
            select(DailyBar)
            .where(DailyBar.instrument_id.in_(instrument_ids))
            .where(DailyBar.value_ts >= start - timedelta(days=5))
            .where(DailyBar.value_ts <= end + timedelta(days=5))
        )
    )
    if not rows:
        return pd.DataFrame(columns=["value_ts", "instrument_id", "next_log_ret"])
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
    df = df.sort_values(["instrument_id", "value_ts"])
    df["next_log_ret"] = (
        df.groupby("instrument_id")["close"]
        .apply(lambda s: np.log(s.shift(-1) / s))
        .reset_index(level=0, drop=True)
    )
    return df[["value_ts", "instrument_id", "next_log_ret"]].dropna()


def compute_attribution(
    session: Session,
    *,
    regime_method_id: str,
    signal_method_ids: list[str],
    as_of: datetime,
    lookback_days: int = 252,
    min_observations: int = 20,
) -> list[dict[str, object]]:
    """Compute attribution rows for one regime method against many
    signal methods. Persists rows to ``regime.regime_attribution``
    via on-conflict-update. Returns the list of persisted rows."""
    start = as_of - timedelta(days=lookback_days + 30)
    labels = _load_regime_labels(session, regime_method_id, start=start, end=as_of)
    if labels.empty:
        log.info(
            "regime.attribution.no_labels",
            regime_method_id=regime_method_id,
        )
        return []

    instruments = list(
        session.scalars(
            select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
        )
    )

    rows_out: list[dict[str, object]] = []
    for signal_id in signal_method_ids:
        signals = _load_signal_values(
            session, signal_id, start=start, end=as_of
        )
        if signals.empty:
            continue
        rets = _load_next_day_returns(
            session, instruments, start=start, end=as_of
        )
        if rets.empty:
            continue
        merged = signals.merge(
            rets, on=["value_ts", "instrument_id"], how="inner"
        )
        # Position direction = sign of signal; PnL per row.
        merged["position_return"] = (
            np.sign(merged["raw_value"]) * merged["next_log_ret"]
        )
        # Attach regime labels (latest at-or-before).
        merged = merged.sort_values("value_ts")
        merged["regime_label"] = merged["value_ts"].map(
            labels.reindex(merged["value_ts"]).ffill()
        )
        merged = merged.dropna(subset=["regime_label"])

        for regime_label, group in merged.groupby("regime_label"):
            if len(group) < min_observations:
                continue
            mean_return = float(group["position_return"].mean())
            std_return = float(group["position_return"].std(ddof=1)) or 1e-9
            sharpe = float(mean_return / std_return * np.sqrt(ANNUALIZATION))
            hit_rate = float(
                (
                    np.sign(group["raw_value"]) == np.sign(group["next_log_ret"])
                ).mean()
            )
            rows_out.append(
                {
                    "regime_method_id": regime_method_id,
                    "regime_label": str(regime_label),
                    "signal_method_id": signal_id,
                    "value_ts": as_of,
                    "n_observations": len(group),
                    "mean_return": mean_return,
                    "sharpe": sharpe,
                    "hit_rate": hit_rate,
                    "attribution_metadata": {
                        "lookback_days": lookback_days,
                        "min_observations": min_observations,
                    },
                }
            )

    if rows_out:
        stmt = pg_insert(RegimeAttribution).values(rows_out)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                "regime_method_id",
                "regime_label",
                "signal_method_id",
                "value_ts",
            ],
            set_={
                "n_observations": stmt.excluded.n_observations,
                "mean_return": stmt.excluded.mean_return,
                "sharpe": stmt.excluded.sharpe,
                "hit_rate": stmt.excluded.hit_rate,
                "attribution_metadata": stmt.excluded.attribution_metadata,
            },
        )
        session.execute(stmt)
        session.flush()
    return rows_out


__all__ = ["compute_attribution"]
