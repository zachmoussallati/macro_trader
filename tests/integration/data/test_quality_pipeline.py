"""Integration test for the daily data-quality pipeline.

Seeds an instrument + a synthetic daily-bar series with one outlier,
registers the methods, runs the daily pipeline, and verifies that flag +
comparison rows land in the right tables.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select

from macro_trader.data.instruments import upsert_instrument
from macro_trader.data.quality.register import register
from macro_trader.data.quality.runner import run_daily_quality_check
from macro_trader.db.models.market_data import DailyBar
from macro_trader.db.models.system import (
    DataQualityFlag,
    MethodComparisonRow,
)
from macro_trader.utils.dates import utcnow


def _seed_instrument_and_bars(session, instrument_id: str = "ZZ") -> None:
    upsert_instrument(
        session,
        instrument_id=instrument_id,
        name="Synthetic",
        asset_class="energy",
        proxy_ticker="ZZZ",
        proxy_type="etf",
    )
    rng = np.random.default_rng(0)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    now = utcnow()
    for i in range(120):
        value_ts = base + timedelta(days=i)
        close = float(rng.normal(loc=50.0, scale=1.0))
        if i == 100:
            close = 500.0  # outlier
        session.add(
            DailyBar(
                instrument_id=instrument_id,
                value_ts=value_ts,
                observation_ts=now,
                close=close,
                source="test",
            )
        )
    session.flush()


@pytest.mark.integration
def test_daily_quality_runner_persists_flags_and_comparison(db_session) -> None:
    _seed_instrument_and_bars(db_session)
    register(db_session)

    summary = run_daily_quality_check(db_session)
    assert "ZZ" in summary
    # Baseline z-score should fire on the inserted outlier.
    assert summary["ZZ"]["data_quality.zscore.v1"] >= 1
    assert "data_quality.isoforest.v1" in summary["ZZ"]

    # Flag rows exist for both methods.
    flag_count = db_session.scalar(select(DataQualityFlag.method_id).limit(1))
    assert flag_count is not None

    # At least one comparison row.
    cmp_rows = list(db_session.scalars(select(MethodComparisonRow)))
    assert any(r.component == "data_quality" for r in cmp_rows)
