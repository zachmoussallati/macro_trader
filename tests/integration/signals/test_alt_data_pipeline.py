"""Alt-data pipeline smoke (Stage 6 Phase 0.3 — Stage 5 deferred).

Seeds EIA + USDA + Google Trends tables with synthetic histories,
runs the daily alt-data runner, and verifies all three methods
produce rows. Uncovered instruments emit zero-confidence rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.data.instruments import upsert_instrument
from macro_trader.db.models.alt_data import (
    EIAInventory,
    GoogleTrends,
    USDAReport,
)
from macro_trader.db.models.signals import SignalValue
from macro_trader.methods.setup import register_all_methods
from macro_trader.signals.alt_data.runner import run_daily_alt_data


def _seed_eia(db_session) -> None:
    rng = np.random.default_rng(0)
    now = datetime.now(UTC)
    rows = []
    for series_id in ("PET.WCRSTUS1.W", "NG.NW2_EPG0_SWO_R48_BCF.W"):
        for w in range(160):
            value_ts = now - timedelta(weeks=160 - w)
            rows.append(
                {
                    "series_id": series_id,
                    "value_ts": value_ts,
                    "observation_ts": value_ts,
                    "value": float(400.0 + rng.normal(scale=10)),
                    "units": "Thousand Barrels",
                    "affected_instruments": ["CL", "BZ"]
                    if "WCRS" in series_id
                    else ["NG"],
                    "source": "eia",
                }
            )
    db_session.execute(
        pg_insert(EIAInventory)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["series_id", "value_ts", "observation_ts"]
        )
    )
    db_session.flush()


def _seed_usda(db_session) -> None:
    rng = np.random.default_rng(1)
    now = datetime.now(UTC)
    rows = []
    for commodity, instrument in (
        ("corn", "ZC"),
        ("soybeans", "ZS"),
        ("wheat", "ZW"),
    ):
        for m in range(40):
            value_ts = now - timedelta(days=30 * (40 - m))
            rows.append(
                {
                    "report_type": "wasde",
                    "value_ts": value_ts,
                    "observation_ts": value_ts,
                    "commodity": commodity,
                    "metric": "production",
                    "value": float(15_000 + m * 50 + rng.normal(scale=200)),
                    "units": "BU",
                    "affected_instruments": [instrument],
                    "source": "usda",
                }
            )
    db_session.bulk_insert_mappings(USDAReport, rows)
    db_session.flush()


def _seed_trends(db_session) -> None:
    rng = np.random.default_rng(2)
    now = datetime.now(UTC)
    rows = []
    for query in ("oil price", "gold", "natural gas"):
        for d in range(180):
            value_ts = now - timedelta(days=180 - d)
            rows.append(
                {
                    "query_term": query,
                    "region": "US",
                    "value_ts": value_ts,
                    "observation_ts": value_ts,
                    "value": float(50 + rng.normal(scale=10)),
                    "affected_instruments": [],
                    "source": "google_trends",
                }
            )
    db_session.execute(
        pg_insert(GoogleTrends)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["query_term", "region", "value_ts", "observation_ts"]
        )
    )
    db_session.flush()


@pytest.mark.integration
def test_alt_data_pipeline_emits_three_methods(db_session) -> None:
    """End-to-end: seed the three alt-data source tables, run the
    runner, verify rows for each method and that uncovered
    instruments emit zero confidence."""
    register_all_methods(db_session)
    universe = ["CL", "BZ", "NG", "ZC", "ZS", "ZW", "GC", "HG", "GLD"]
    for inst in universe:
        upsert_instrument(
            db_session,
            instrument_id=inst,
            name=inst,
            asset_class="energy",
            proxy_ticker=inst,
            proxy_type="etf",
        )
    _seed_eia(db_session)
    _seed_usda(db_session)
    _seed_trends(db_session)
    db_session.commit()

    written = run_daily_alt_data(db_session, instruments=universe)
    for method_id in (
        "alt_data.eia_storage.v1",
        "alt_data.usda_wasde.v1",
        "alt_data.google_trends.v1",
    ):
        assert written.get(method_id, 0) > 0
        rows = list(
            db_session.scalars(
                select(SignalValue).where(SignalValue.signal_id == method_id)
            )
        )
        assert rows, f"no rows for {method_id}"

    # Confirm "covered=False" semantics: GLD isn't covered by any
    # alt-data sub-signal, so its rows should all be zero-confidence.
    gld_rows = list(
        db_session.scalars(
            select(SignalValue).where(SignalValue.instrument_id == "GLD")
        )
    )
    assert all(
        (r.confidence is None or float(r.confidence) == 0.0) for r in gld_rows
    ), "expected GLD (uncovered) to have zero confidence on all alt-data rows"
