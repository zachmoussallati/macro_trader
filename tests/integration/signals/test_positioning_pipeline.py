"""End-to-end positioning-signal pipeline test on seeded COT data."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from macro_trader.data.instruments import upsert_instrument
from macro_trader.db.models.positioning import COTWeekly
from macro_trader.db.models.signals import SignalValue
from macro_trader.methods.setup import register_all_methods
from macro_trader.signals.positioning.runner import run_daily_positioning
from macro_trader.utils.dates import utcnow


@pytest.mark.integration
def test_positioning_pipeline_persists_signal_values(db_session) -> None:
    """Seed three years of weekly COT rows for two instruments with
    enough variance for the rolling z-score to produce non-NaN values,
    then verify the daily positioning runner persists ``signal_values``
    rows."""
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

    now = utcnow()
    n_weeks = 160
    base_dt = now - timedelta(weeks=n_weeks - 1)
    for i in range(n_weeks):
        report_ts = base_dt + timedelta(weeks=i)
        # Ramping net long on AA; flat on BB. Both reports the same
        # underlying numbers for both "disaggregated" and "legacy" report
        # types so both methods produce non-empty output series.
        aa_long = 30_000 + i * 200
        aa_short = 30_000
        bb_long = 30_000
        bb_short = 30_000 + i * 100
        for instrument_id, long, short in (
            ("AA", aa_long, aa_short),
            ("BB", bb_long, bb_short),
        ):
            for report_type in ("disaggregated", "legacy"):
                # legacy uses producer_*; disaggregated uses managed_money_*.
                kwargs: dict[str, object] = {
                    "report_ts": report_ts,
                    "instrument_id": instrument_id,
                    "report_type": report_type,
                    "publication_ts": report_ts + timedelta(days=3),
                    "cftc_contract_code": "TEST",
                    "open_interest": 200_000.0,
                    "source": "test",
                }
                if report_type == "disaggregated":
                    kwargs["managed_money_long"] = float(long)
                    kwargs["managed_money_short"] = float(short)
                else:
                    kwargs["producer_long"] = float(long)
                    kwargs["producer_short"] = float(short)
                db_session.add(COTWeekly(**kwargs))
    db_session.flush()

    register_all_methods(db_session)

    written = run_daily_positioning(
        db_session, instruments=["AA", "BB"], window_days=21
    )
    assert written["positioning.cot_zscore.v1"] >= 1
    assert written["positioning.cot_commercial.v1"] >= 1

    rows = list(
        db_session.scalars(
            select(SignalValue).where(SignalValue.signal_id == "positioning.cot_zscore.v1")
        )
    )
    assert any(r.instrument_id == "AA" for r in rows)
    assert any(r.instrument_id == "BB" for r in rows)

    # AA is crowd net long (ramping) => sign-inverted raw_value should be
    # non-positive; BB is crowd net short => non-negative.
    aa_rows = [r for r in rows if r.instrument_id == "AA"]
    bb_rows = [r for r in rows if r.instrument_id == "BB"]
    aa = aa_rows[-1]
    bb = bb_rows[-1]
    assert float(aa.raw_value) <= 0
    assert float(bb.raw_value) >= 0
    # Confidence reflects the abundant history we seeded.
    assert float(aa.confidence) > 0.5
