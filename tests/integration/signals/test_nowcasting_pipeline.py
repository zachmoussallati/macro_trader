"""Nowcasting pipeline smoke (Stage 6 Phase 0.3 — Stage 5 deferred).

Seeds enough FRED macro-series rows for at least one release's
regression to fit, runs both methods through the runner, and
asserts:

- OLS-AR baseline produces signal_values rows for the affected
  instruments.
- BVAR shadow produces rows too.
- The cross-method comparator persists a ComparisonResult.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.data.instruments import upsert_instrument
from macro_trader.db.models.macro_data import Series, SeriesObservation
from macro_trader.db.models.signals import SignalValue
from macro_trader.db.models.system import MethodComparisonRow
from macro_trader.methods.setup import register_all_methods
from macro_trader.signals.nowcasting.runner import run_daily_nowcasting


def _seed_series(
    db_session, series_id: str, *, freq: str, n_points: int, start: datetime
) -> None:
    """Insert n_points monthly observations of a synthetic series."""
    db_session.execute(
        pg_insert(Series)
        .values(
            series_id=series_id,
            name=series_id,
            source="fred",
            frequency=freq,
            units="Index",
            seasonal_adjustment=None,
            category="macro",
            affected_instruments=[],
            series_metadata={},
            is_active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["series_id"])
    )
    rng = np.random.default_rng(hash(series_id) % (2**32))
    rows = []
    for i in range(n_points):
        value_ts = start + timedelta(days=30 * i)
        rows.append(
            {
                "series_id": series_id,
                "value_ts": value_ts,
                "observation_ts": value_ts,
                "realtime_start": value_ts,
                "realtime_end": None,
                "value": float(100.0 + i * 0.5 + rng.normal(scale=0.5)),
                "is_initial": True,
                "revision_number": 0,
                "source": "fred",
                "source_version": None,
            }
        )
    if rows:
        db_session.execute(
            pg_insert(SeriesObservation)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["series_id", "value_ts", "observation_ts"]
            )
        )
    db_session.flush()


@pytest.mark.integration
def test_nowcasting_pipeline_emits_both_methods(db_session) -> None:
    """End-to-end: seed FRED inputs for the NFP release, run the
    daily runner, verify both methods produce rows + a comparator
    row lands."""
    register_all_methods(db_session)
    for inst in ("GC", "SI"):
        upsert_instrument(
            db_session,
            instrument_id=inst,
            name=inst,
            asset_class="precious_metals",
            proxy_ticker={"GC": "GLD", "SI": "SLV"}[inst],
            proxy_type="etf",
        )

    # NFP release fits on FRED:PAYEMS + lead indicators (ICSA, UNRATE).
    start = datetime.now(UTC) - timedelta(days=365 * 6)
    for series_id in ("FRED:PAYEMS", "FRED:ICSA", "FRED:UNRATE"):
        _seed_series(
            db_session, series_id, freq="monthly", n_points=72, start=start
        )
    db_session.commit()

    written = run_daily_nowcasting(db_session, instruments=["GC", "SI", "CL"])
    # NFP affects GC + SI; CL is uncovered.
    assert (
        written.get("nowcasting.ols_ar.v1", 0) > 0
        or written.get("nowcasting.bvar.v1", 0) > 0
    )

    for method_id in ("nowcasting.ols_ar.v1", "nowcasting.bvar.v1"):
        rows = list(
            db_session.scalars(
                select(SignalValue).where(SignalValue.signal_id == method_id)
            )
        )
        assert rows, f"no rows for {method_id}"

    # Cross-method comparator persisted at least one ComparisonResult
    # for the nowcasting_signal component.
    comparisons = list(
        db_session.scalars(
            select(MethodComparisonRow).where(
                MethodComparisonRow.component == "nowcasting_signal"
            )
        )
    )
    assert comparisons, "no ComparisonResult rows for nowcasting_signal"
