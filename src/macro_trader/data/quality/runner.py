"""Daily data-quality runner.

Runs the production (BASELINE) and shadow methods on every active
instrument's daily-bar close series, persists per-(method, series) flags,
and runs the comparator for the day. Persistent records:

- ``system.data_quality_flags`` — one row per (method, series, value_ts).
- ``system.method_comparisons`` — one row per comparator run.

Idempotent: re-running for the same day overwrites the day's flag rows for
each method (delete-then-insert), so reruns don't double-count.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import select

from macro_trader.data.quality.comparator import DataQualityComparator
from macro_trader.data.quality.methods import (
    IsolationForestOutlier,
    ZScoreOutlier,
)
from macro_trader.db.models.market_data import DailyBar, Instrument
from macro_trader.db.models.system import DataQualityFlag
from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import get_production, get_shadows
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


log = get_logger(__name__)


def _load_close_series(
    session: Session, instrument_id: str, lookback_days: int = 365
) -> tuple[list[datetime], np.ndarray]:
    """Return (value_ts list, close array) for the latest vintage per value_ts."""
    cutoff = utcnow() - timedelta(days=lookback_days)
    # For each value_ts, take the latest observation_ts (the most-recent
    # revision we hold).
    stmt = (
        select(DailyBar.value_ts, DailyBar.close, DailyBar.observation_ts)
        .where(DailyBar.instrument_id == instrument_id)
        .where(DailyBar.value_ts >= cutoff)
        .order_by(DailyBar.value_ts, DailyBar.observation_ts.desc())
    )
    rows = list(session.execute(stmt).all())
    # Dedupe by value_ts keeping the first (latest observation_ts).
    by_ts: dict[datetime, float] = {}
    for value_ts, close, _obs in rows:
        if value_ts in by_ts:
            continue
        by_ts[value_ts] = float(close) if close is not None else float("nan")
    timestamps = sorted(by_ts.keys())
    arr = np.array([by_ts[t] for t in timestamps], dtype=float)
    return timestamps, arr


def _persist_flags(
    session: Session,
    *,
    method_id: str,
    instrument_id: str,
    timestamps: list[datetime],
    values: np.ndarray,
    flags: np.ndarray,
    run_at: datetime,
) -> int:
    """Delete-then-insert the flags for ``run_at`` date to keep reruns idempotent."""
    if not timestamps:
        return 0
    run_day_start = run_at.replace(hour=0, minute=0, second=0, microsecond=0)
    run_day_end = run_day_start + timedelta(days=1)
    series_id = f"market_data.daily_bars:{instrument_id}:close"
    session.query(DataQualityFlag).filter(
        DataQualityFlag.method_id == method_id,
        DataQualityFlag.series_id == series_id,
        DataQualityFlag.run_at >= run_day_start,
        DataQualityFlag.run_at < run_day_end,
    ).delete(synchronize_session=False)
    payload = []
    for ts, value, is_flagged in zip(timestamps, values, flags, strict=True):
        if not bool(is_flagged):
            continue
        payload.append(
            {
                "flag_id": uuid.uuid4(),
                "method_id": method_id,
                "series_id": series_id,
                "value_ts": ts,
                "value": float(value) if not np.isnan(value) else None,
                "is_flagged": True,
                "confidence": None,
                "run_at": run_at,
                "lineage_id": None,
            }
        )
    if payload:
        from sqlalchemy import inspect

        session.bulk_insert_mappings(inspect(DataQualityFlag), payload)
    return len(payload)


def run_daily_quality_check(session: Session) -> dict[str, dict[str, int]]:
    """Run today's data-quality pipeline over every active instrument.

    Returns a per-instrument summary: ``{instrument_id: {method_id: n_flags}}``.
    """
    run_at = utcnow()
    summary: dict[str, dict[str, int]] = defaultdict(dict)

    baseline = get_production("data_quality")
    shadows = get_shadows("data_quality")

    # Ensure shadows have fitted estimators where applicable.
    instruments = list(session.scalars(select(Instrument).where(Instrument.is_active.is_(True))))
    comparator = DataQualityComparator()

    for instrument in instruments:
        timestamps, values = _load_close_series(session, instrument.instrument_id)
        if values.size == 0:
            continue

        # Baseline (no-op fit; predict immediately).
        baseline.fit(values)
        baseline_flags = baseline.predict(values)
        n = _persist_flags(
            session,
            method_id=baseline.metadata.method_id,
            instrument_id=instrument.instrument_id,
            timestamps=timestamps,
            values=values,
            flags=baseline_flags,
            run_at=run_at,
        )
        summary[instrument.instrument_id][baseline.metadata.method_id] = n

        # Shadows.
        for shadow in shadows:
            shadow.fit(values)
            shadow_flags = shadow.predict(values)
            n = _persist_flags(
                session,
                method_id=shadow.metadata.method_id,
                instrument_id=instrument.instrument_id,
                timestamps=timestamps,
                values=values,
                flags=shadow_flags,
                run_at=run_at,
            )
            summary[instrument.instrument_id][shadow.metadata.method_id] = n

            # Comparator run for (baseline, this shadow) on this instrument.
            comparator.compare(
                baseline,
                shadow,
                values,
                period_start=timestamps[0] if timestamps else run_at,
                period_end=timestamps[-1] if timestamps else run_at,
                notes=f"daily quality for {instrument.instrument_id}",
                session=session,
            )

    session.commit()
    log.info(
        "data.quality.daily_run.complete",
        instruments=len(summary),
        sample=dict(list(summary.items())[:3]),
    )
    return dict(summary)


# Fallback for callers that need the methods directly without going through
# the registry (e.g. tests that don't register methods).
def _fallback_methods() -> tuple[ZScoreOutlier, IsolationForestOutlier]:
    return ZScoreOutlier(), IsolationForestOutlier()
