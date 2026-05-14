"""End-to-end trend-signal pipeline test on seeded synthetic data."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy import select

from macro_trader.data.instruments import upsert_instrument
from macro_trader.db.models.market_data import DailyBar
from macro_trader.db.models.signals import SignalValue
from macro_trader.methods.setup import register_all_methods
from macro_trader.signals.trend.runner import run_daily_trend
from macro_trader.utils.dates import utcnow


@pytest.mark.integration
def test_trend_pipeline_persists_signal_values(db_session) -> None:
    # Seed two instruments with ~300 days of synthetic trending data.
    upsert_instrument(
        db_session,
        instrument_id="AA",
        name="A",
        asset_class="energy",
        proxy_ticker="AAA",
        proxy_type="etf",
    )
    upsert_instrument(
        db_session,
        instrument_id="BB",
        name="B",
        asset_class="energy",
        proxy_ticker="BBB",
        proxy_type="etf",
    )

    rng = np.random.default_rng(0)
    n_days = 300
    now = utcnow()
    # Daily bars land at midnight UTC in production (yfinance trading-date
    # aligned). Mirror that here so the calendar reindex matches.
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    base_date = today_midnight - timedelta(days=n_days - 1)
    for i in range(n_days):
        value_ts = base_date + timedelta(days=i)
        # AA: upward drift; BB: downward drift.
        aa_close = float(100 + i * 0.1 + rng.normal(scale=0.5))
        bb_close = float(100 - i * 0.05 + rng.normal(scale=0.5))
        db_session.add(
            DailyBar(
                instrument_id="AA",
                value_ts=value_ts,
                observation_ts=now,
                close=aa_close,
                source="test",
            )
        )
        db_session.add(
            DailyBar(
                instrument_id="BB",
                value_ts=value_ts,
                observation_ts=now,
                close=bb_close,
                source="test",
            )
        )
    db_session.flush()

    # Signals reference system.methods_registry via FK — register first.
    register_all_methods(db_session)

    written = run_daily_trend(db_session, instruments=["AA", "BB"], window_days=14)

    # Every trend method should have written rows.
    assert written["trend.sma_short.v1"] >= 1
    assert written["trend.sma_medium.v1"] >= 1
    assert written["trend.ensemble.v1"] >= 1
    assert written["trend.hp_filter.v1"] >= 1

    # Pick one ensemble row per instrument and verify shape.
    rows = list(
        db_session.scalars(select(SignalValue).where(SignalValue.signal_id == "trend.ensemble.v1"))
    )
    assert any(r.instrument_id == "AA" for r in rows)
    assert any(r.instrument_id == "BB" for r in rows)
    aa = [r for r in rows if r.instrument_id == "AA"][-1]
    bb = [r for r in rows if r.instrument_id == "BB"][-1]
    assert aa.raw_value is not None
    assert bb.raw_value is not None
    # Up-drift instrument should have a positive ensemble signal; down-drift negative.
    assert float(aa.raw_value) > 0
    assert float(bb.raw_value) < 0
    # Rank in [0, 1].
    assert 0 <= float(aa.rank) <= 1
    assert 0 <= float(bb.rank) <= 1
