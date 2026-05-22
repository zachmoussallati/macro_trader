"""Full-stack smoke with regime classification + attribution
(Stage 7 Phase 0.4).

Different from ``signals/test_full_stack_smoke.py`` (registration
only) and ``test_regime_pipeline.py`` (regime methods only). This
combines:

1. Seed market + macro data for one signal family (trend) + the
   regime feature panel.
2. Run trend signal computation → ``signals.signal_values`` rows.
3. Run regime classification → ``regime.regime_states`` rows.
4. Run regime attribution → ``regime.regime_attribution`` rows.
5. Verify at least one attribution row exists per (regime_method,
   regime_label, signal_method) bucket with enough observations.

Per-family signal coverage is exercised by each family's pipeline
test; this smoke proves the *integration plumbing* (signal_values
×  regime_states → attribution) is wired correctly. Stage 7 composite
scoring depends on this same plumbing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.data.instruments import upsert_instrument
from macro_trader.db.models.macro_data import Series, SeriesObservation
from macro_trader.db.models.market_data import DailyBar
from macro_trader.db.models.regime import RegimeAttribution, RegimeState
from macro_trader.db.models.signals import SignalValue
from macro_trader.db.models.system import MethodRegistryRow
from macro_trader.methods.setup import register_all_methods
from macro_trader.regime.attribution import compute_attribution
from macro_trader.regime.runner import run_daily_regime_classification
from macro_trader.signals.trend.runner import run_daily_trend
from macro_trader.utils.dates import utcnow

FRED_FOR_REGIME: tuple[tuple[str, str, float, float, float], ...] = (
    # (series_id, freq, base, drift_per_step, noise_scale)
    ("FRED:INDPRO", "monthly", 100.0, 0.2, 0.5),
    ("FRED:CPIAUCSL", "monthly", 250.0, 0.5, 0.3),
    ("FRED:DFII2", "daily", 1.5, 0.0, 0.05),
    ("FRED:DTWEXBGS", "daily", 100.0, 0.05, 0.5),
    ("FRED:DCOILWTICO", "daily", 80.0, 0.0, 1.0),
    ("FRED:VIXCLS", "daily", 18.0, 0.0, 4.0),
    ("FRED:DGS10", "daily", 2.0, 0.0, 0.2),
    ("FRED:DGS2", "daily", 1.5, 0.0, 0.15),
)


def _seed_series(
    db_session,
    series_id: str,
    *,
    freq: str,
    n_points: int,
    step_days: int,
    start: datetime,
    base: float,
    drift_per_step: float,
    noise_scale: float,
) -> None:
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
        value_ts = start + timedelta(days=step_days * i)
        rows.append(
            {
                "series_id": series_id,
                "value_ts": value_ts,
                "observation_ts": value_ts,
                "realtime_start": value_ts,
                "realtime_end": None,
                "value": float(base + i * drift_per_step + rng.normal(scale=noise_scale)),
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


def _seed_bars(
    db_session,
    instrument_id: str,
    *,
    asset_class: str,
    proxy_ticker: str,
    n_days: int,
    base: float,
    drift_per_step: float,
    noise_scale: float,
    now: datetime,
    seed_offset: int = 0,
) -> None:
    upsert_instrument(
        db_session,
        instrument_id=instrument_id,
        name=instrument_id,
        asset_class=asset_class,
        proxy_ticker=proxy_ticker,
        proxy_type="etf",
    )
    rng = np.random.default_rng((hash(instrument_id) + seed_offset) % (2**32))
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    base_date = today_midnight - timedelta(days=n_days - 1)
    rows = []
    for i in range(n_days):
        value_ts = base_date + timedelta(days=i)
        rows.append(
            DailyBar(
                instrument_id=instrument_id,
                value_ts=value_ts,
                observation_ts=now,
                close=float(base + i * drift_per_step + rng.normal(scale=noise_scale)),
                source="test",
            )
        )
    db_session.add_all(rows)
    db_session.flush()


@pytest.mark.integration
def test_full_stack_with_regime_and_attribution(db_session) -> None:
    """Trend signal → regime classification → attribution end-to-end."""
    register_all_methods(db_session)

    now = utcnow()
    n_days_bars = 400
    n_days_fred = 900

    # Macro / regime panel inputs.
    for series_id, freq, base, drift, noise in FRED_FOR_REGIME:
        if freq == "monthly":
            n_points = n_days_fred // 30
            step_days = 30
            start = now - timedelta(days=step_days * n_points)
        else:
            n_points = n_days_fred
            step_days = 1
            start = now - timedelta(days=n_days_fred)
        _seed_series(
            db_session,
            series_id,
            freq=freq,
            n_points=n_points,
            step_days=step_days,
            start=start,
            base=base,
            drift_per_step=drift,
            noise_scale=noise,
        )

    # Regime-feature ETFs (SPY/HYG/IEF for realized_vol / credit_spread).
    for inst_id, base, drift in (("SPY", 400.0, 0.05), ("HYG", 80.0, 0.0), ("IEF", 100.0, 0.0)):
        _seed_bars(
            db_session,
            inst_id,
            asset_class="macro_proxy",
            proxy_ticker=inst_id,
            n_days=n_days_bars,
            base=base,
            drift_per_step=drift,
            noise_scale=base * 0.005,
            now=now,
        )

    # Signal instruments for trend (use a couple commodities so
    # signal_values has more than one bucket per regime).
    _seed_bars(
        db_session,
        "GC",
        asset_class="precious_metals",
        proxy_ticker="GLD",
        n_days=n_days_bars,
        base=180.0,
        drift_per_step=0.05,
        noise_scale=0.8,
        now=now,
    )
    _seed_bars(
        db_session,
        "SI",
        asset_class="precious_metals",
        proxy_ticker="SLV",
        n_days=n_days_bars,
        base=22.0,
        drift_per_step=-0.005,
        noise_scale=0.2,
        now=now,
        seed_offset=1,
    )
    db_session.commit()

    # 1. Run trend signals — fills signals.signal_values.
    trend_written = run_daily_trend(
        db_session, instruments=["GC", "SI"], window_days=120
    )
    assert trend_written.get("trend.ensemble.v1", 0) > 0
    db_session.flush()

    # 2. Run regime classification — fills regime.regime_states.
    regime_written = run_daily_regime_classification(db_session)
    db_session.flush()
    assert regime_written.get("regime.rules.v1", 0) > 0

    n_regime_rows = db_session.scalar(
        select(RegimeState.method_id).limit(1)
    )
    assert n_regime_rows is not None

    # 3. Run attribution — fills regime.regime_attribution. Use the
    # trend ensemble as the signal in the attribution bucket so we
    # exercise the cross-join end-to-end.
    signal_method_ids = [
        r.method_id
        for r in db_session.scalars(
            select(MethodRegistryRow).where(
                MethodRegistryRow.component == "trend_signal"
            )
        )
    ]
    assert "trend.ensemble.v1" in signal_method_ids

    attr_rows = compute_attribution(
        db_session,
        regime_method_id="regime.rules.v1",
        signal_method_ids=signal_method_ids,
        as_of=now,
        lookback_days=365,
        min_observations=1,  # synthetic data — relax floor
    )
    db_session.flush()

    # At least one (regime_label, signal_method) bucket got attribution.
    persisted = list(db_session.scalars(select(RegimeAttribution)))
    assert persisted, (
        f"no RegimeAttribution rows persisted; compute_attribution "
        f"returned {len(attr_rows)} rows"
    )
    # And signal_values exist for the methods we asked attribution to
    # cover (sanity check the precondition).
    sv = list(
        db_session.scalars(
            select(SignalValue).where(
                SignalValue.signal_id == "trend.ensemble.v1"
            )
        )
    )
    assert sv, "no trend.ensemble.v1 SignalValue rows — attribution upstream missing"
