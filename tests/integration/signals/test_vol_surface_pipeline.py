"""Vol-surface pipeline smoke (Stage 6 Phase 0.3 — Stage 5 deferred).

Seeds the options_chains table directly, runs the daily vol-surface
runner end-to-end, and verifies:

- Rows land in ``signals.signal_values`` for both
  ``vol_surface.raw.v1`` and ``vol_surface.svi.v1``.
- Each row carries ``historical_backtest_supported: False`` in its
  metadata (the central Stage 5 architectural promise).
- Only the 6 covered ETFs (GLD/SLV/USO/UNG/DBA/SPY) get non-zero
  confidence; other instruments emit zero-confidence rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.data.instruments import upsert_instrument
from macro_trader.db.models.market_data import OptionsChain
from macro_trader.db.models.signals import SignalValue
from macro_trader.methods.setup import register_all_methods
from macro_trader.signals.vol_surface.methods import VOL_SURFACE_UNIVERSE
from macro_trader.signals.vol_surface.runner import run_daily_vol_surface


def _seed_chain(
    db_session, instrument_id: str, snapshot_ts: datetime, spot: float = 100.0
) -> int:
    """Insert a synthetic chain with 7 strikes at one near-term expiry."""
    expiry_ts = snapshot_ts + timedelta(days=30)
    strikes = np.linspace(spot * 0.90, spot * 1.10, 7)
    rows = []
    for strike in strikes:
        for option_type in ("call", "put"):
            # Quadratic smile around ATM = 20%.
            iv = 0.20 + 0.5 * (np.log(strike / spot)) ** 2
            rows.append(
                {
                    "instrument_id": instrument_id,
                    "snapshot_ts": snapshot_ts,
                    "expiry_ts": expiry_ts,
                    "strike": float(strike),
                    "option_type": option_type,
                    "bid": 1.0,
                    "ask": 1.1,
                    "last": 1.05,
                    "volume": 100,
                    "open_interest": 500,
                    "implied_vol": float(iv),
                    "delta": 0.5,
                    "gamma": 0.05,
                    "vega": 0.20,
                    "theta": -0.01,
                    "underlying_price": float(spot),
                    "source": "test",
                }
            )
    db_session.execute(
        pg_insert(OptionsChain)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=[
                "instrument_id",
                "snapshot_ts",
                "expiry_ts",
                "strike",
                "option_type",
            ]
        )
    )
    db_session.flush()
    return len(rows)


@pytest.mark.integration
def test_vol_surface_pipeline_emits_both_methods(db_session) -> None:
    """End-to-end: seed chains for 2 covered ETFs, run the daily
    runner, verify both methods produce rows with the architectural
    metadata flag."""
    register_all_methods(db_session)

    # Seed 2 of the 6 covered ETFs (GLD, USO).
    for inst in ("GLD", "USO"):
        upsert_instrument(
            db_session,
            instrument_id=inst,
            name=inst,
            asset_class="energy",
            proxy_ticker=inst,
            proxy_type="etf",
        )

    snapshot_ts = datetime.now(UTC).replace(microsecond=0)
    _seed_chain(db_session, "GLD", snapshot_ts, spot=180.0)
    _seed_chain(db_session, "USO", snapshot_ts, spot=75.0)
    db_session.commit()

    written = run_daily_vol_surface(
        db_session, instruments=["GLD", "USO", "CL"]  # CL has no chain
    )
    assert written.get("vol_surface.raw.v1", 0) > 0
    assert written.get("vol_surface.svi.v1", 0) > 0

    for method_id in ("vol_surface.raw.v1", "vol_surface.svi.v1"):
        rows = list(
            db_session.scalars(
                select(SignalValue).where(SignalValue.signal_id == method_id)
            )
        )
        assert rows, f"no rows for {method_id}"
        for r in rows:
            meta = r.signal_metadata if isinstance(r.signal_metadata, dict) else {}
            assert meta.get("historical_backtest_supported") is False, (
                f"{method_id} missing historical_backtest_supported flag"
            )
            assert meta.get("data_source") == "yfinance"

        # Coverage check: GLD + USO get non-zero confidence; CL (uncovered)
        # gets zero confidence.
        by_inst = {r.instrument_id: r for r in rows}
        for covered in ("GLD", "USO"):
            assert (
                by_inst[covered].confidence is not None
                and float(by_inst[covered].confidence) > 0
            )
        assert "CL" in by_inst, "uncovered instrument should still emit a row"
        assert float(by_inst["CL"].confidence or 0) == 0.0


@pytest.mark.integration
def test_vol_surface_universe_is_six_etfs() -> None:
    """Documents the Stage 5 universe; no DB use."""
    assert VOL_SURFACE_UNIVERSE == ("GLD", "SLV", "USO", "UNG", "DBA", "SPY")
