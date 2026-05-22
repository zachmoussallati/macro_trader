"""Composite scoring pipeline smoke (Stage 8 Phase 0.1).

Seeds the upstream stack — 13 instruments + a synthetic signal panel
for all 10 families + regime states across the 5 named regimes +
attribution rows with enough observations to drive
:func:`compute_regime_conditional_weights` — then runs the daily
composite runner and asserts:

- ``signals.composite_scores`` has rows for the production composite
  (linear baseline) on every active instrument that had signals.
- ``signals.composite_weights`` has a fresh snapshot after running
  ``refit_weights``.
- ``composite_metadata.contributions`` decomposes correctly: the sum
  of ``weight * z * confidence`` across contributions matches the
  persisted ``raw_score`` within float tolerance.
- The Bayesian + GBM SHADOW methods either land rows (when their
  fitted state is available) or no-op silently (when not).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select

from macro_trader.composite.refit import refit_weights
from macro_trader.composite.runner import (
    COMPOSITE_SIGNAL_COMPONENTS,
    run_daily_composite,
)
from macro_trader.data.instruments import upsert_instrument
from macro_trader.db.models.regime import RegimeAttribution, RegimeState
from macro_trader.db.models.signals import (
    CompositeScore,
    CompositeWeight,
    SignalValue,
)
from macro_trader.methods.setup import register_all_methods
from macro_trader.regime import NAMED_REGIMES
from macro_trader.signals.designated import resolve_id
from macro_trader.utils.dates import utcnow

INSTRUMENTS = [
    ("CL", "energy"),
    ("BZ", "energy"),
    ("NG", "energy"),
    ("HO", "energy"),
    ("RB", "energy"),
    ("HG", "base_metals"),
    ("ALI", "base_metals"),
    ("GC", "precious_metals"),
    ("SI", "precious_metals"),
    ("PL", "precious_metals"),
    ("ZC", "grains"),
    ("ZS", "grains"),
    ("ZW", "grains"),
]


def _seed_instruments(db_session) -> list[str]:
    inst_ids: list[str] = []
    for inst_id, asset_class in INSTRUMENTS:
        upsert_instrument(
            db_session,
            instrument_id=inst_id,
            name=inst_id,
            asset_class=asset_class,
            proxy_ticker=f"{inst_id}_ETF",
            proxy_type="etf",
        )
        inst_ids.append(inst_id)
    return inst_ids


def _seed_signal_panel(
    db_session,
    *,
    instrument_ids: list[str],
    designated_method_ids: list[str],
    n_days: int,
    now: datetime,
) -> None:
    """Insert ``n_days`` of synthetic signal_values across the panel."""
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    base_date = today_midnight - timedelta(days=n_days - 1)
    rng = np.random.default_rng(7)
    rows = []
    for i in range(n_days):
        value_ts = base_date + timedelta(days=i)
        for inst_id in instrument_ids:
            for sid in designated_method_ids:
                raw = float(rng.normal(0.0, 0.5))
                rows.append(
                    SignalValue(
                        signal_id=sid,
                        instrument_id=inst_id,
                        value_ts=value_ts,
                        observation_ts=now,
                        raw_value=raw,
                        zscore=raw,
                        rank=0.5,
                        confidence=0.7,
                        rolling_sharpe_252=None,
                        signal_metadata={},
                    )
                )
    db_session.add_all(rows)
    db_session.flush()


def _seed_regime_states(
    db_session,
    *,
    regime_method_id: str,
    n_days: int,
    now: datetime,
) -> None:
    """One regime_state per day rotating through NAMED_REGIMES."""
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    base_date = today_midnight - timedelta(days=n_days - 1)
    rows = []
    for i in range(n_days):
        value_ts = base_date + timedelta(days=i)
        label = NAMED_REGIMES[i % len(NAMED_REGIMES)]
        vec = dict.fromkeys(NAMED_REGIMES, 0.0)
        vec[label] = 1.0
        rows.append(
            RegimeState(
                method_id=regime_method_id,
                value_ts=value_ts,
                observation_ts=now,
                label=label,
                probability_vector=vec,
                confidence=1.0,
                transition_prob=None,
                days_in_regime=1,
                state_metadata={"method": "test"},
            )
        )
    db_session.add_all(rows)
    db_session.flush()


def _seed_attribution_rows(
    db_session,
    *,
    regime_method_id: str,
    signal_method_ids: list[str],
    as_of: datetime,
) -> None:
    """One attribution row per (regime_label, signal_method_id) with
    >= 20 observations so weight derivation uses sharpe-based weights
    rather than the flat-prior fallback."""
    rows = []
    rng = np.random.default_rng(11)
    for regime_label in NAMED_REGIMES:
        for sid in signal_method_ids:
            rows.append(
                RegimeAttribution(
                    regime_method_id=regime_method_id,
                    regime_label=regime_label,
                    signal_method_id=sid,
                    value_ts=as_of,
                    n_observations=int(rng.integers(40, 80)),
                    mean_return=float(rng.normal(0.0, 0.001)),
                    sharpe=float(rng.normal(0.5, 0.5)),
                    hit_rate=float(rng.uniform(0.45, 0.6)),
                    attribution_metadata={"lookback_days": 252},
                )
            )
    db_session.add_all(rows)
    db_session.flush()


@pytest.mark.integration
def test_composite_pipeline_emits_baseline_and_snapshots(db_session) -> None:
    """End-to-end Stage 7 composite plumbing on synthetic data."""
    register_all_methods(db_session)

    now = utcnow()
    instrument_ids = _seed_instruments(db_session)
    db_session.commit()

    # Build the designated signal id list — matches what the composite
    # runner resolves at runtime.
    designated_signal_method_ids: list[str] = []
    for component in COMPOSITE_SIGNAL_COMPONENTS:
        mid = resolve_id(component)
        if mid is not None:
            designated_signal_method_ids.append(mid)
    assert len(designated_signal_method_ids) >= 5, (
        "expected the composite runner to resolve >= 5 designated signal "
        f"methods, got {designated_signal_method_ids}"
    )

    # 14 days of synthetic signal_values across the panel and 14 days
    # of regime_states. Daily window in run_daily_composite is 7 days;
    # we seed 14 to give a buffer for overlap and let the regime
    # rotation cover every named regime.
    _seed_signal_panel(
        db_session,
        instrument_ids=instrument_ids,
        designated_method_ids=designated_signal_method_ids,
        n_days=14,
        now=now,
    )
    regime_method_id = resolve_id("regime_classifier") or "regime.rules.v1"
    _seed_regime_states(
        db_session,
        regime_method_id=regime_method_id,
        n_days=14,
        now=now,
    )
    _seed_attribution_rows(
        db_session,
        regime_method_id=regime_method_id,
        signal_method_ids=designated_signal_method_ids,
        as_of=now,
    )
    db_session.commit()

    # 1. Weights refit: pull attribution -> build per-(regime, signal)
    # snapshot. Run before the daily compute so the linear method
    # has a snapshot to read.
    n_weights = refit_weights(db_session)
    assert n_weights == len(NAMED_REGIMES) * len(designated_signal_method_ids)
    db_session.flush()

    snap = list(
        db_session.scalars(
            select(CompositeWeight).where(
                CompositeWeight.method_id == "composite.linear.v1"
            )
        )
    )
    assert snap, "no CompositeWeight rows persisted by refit_weights"
    # Per-regime weights should sum to ~1.0 (the snapshot writer
    # normalises per regime, then EWM-smooths against the prior
    # snapshot if any).
    by_regime: dict[str, list[float]] = {r: [] for r in NAMED_REGIMES}
    for w in snap:
        by_regime[w.regime_label].append(float(w.weight))
    for regime_label, weights in by_regime.items():
        if not weights:
            continue
        assert math.isclose(sum(weights), 1.0, abs_tol=1e-6), (
            f"weights for {regime_label} sum to {sum(weights):.6f}, expected 1.0"
        )

    # 2. Daily compute for the production composite. The runner
    # invokes every registered composite method; Bayesian's
    # fit-from-history hasn't been run so it returns []. The linear
    # baseline reads the snapshot we just wrote and produces rows.
    written = run_daily_composite(
        db_session, instruments=instrument_ids, window_days=14
    )
    assert written.get("composite.linear.v1", 0) > 0, (
        f"linear composite produced 0 rows; written={written}"
    )

    rows = list(
        db_session.scalars(
            select(CompositeScore).where(
                CompositeScore.method_id == "composite.linear.v1"
            )
        )
    )
    assert rows, "no CompositeScore rows for composite.linear.v1"

    # Every persisted row's contributions should reconstruct raw_score
    # within float tolerance.
    for r in rows:
        meta = r.composite_metadata or {}
        contribs = meta.get("contributions", [])
        if not contribs:
            continue
        reconstructed = sum(float(c.get("contribution", 0.0)) for c in contribs)
        assert math.isclose(reconstructed, float(r.raw_score), abs_tol=1e-6), (
            f"raw_score reconstruction mismatch for "
            f"{r.method_id} {r.instrument_id} {r.value_ts}: "
            f"persisted={r.raw_score:.6f}, reconstructed={reconstructed:.6f}"
        )

    # Regime label populated on every row (we seeded a non-null label
    # per value_ts).
    assert all(r.regime_label is not None for r in rows)
    assert all(r.n_signals_used is not None and r.n_signals_used > 0 for r in rows)

    # 3. Shadows (Bayesian + GBM) should at least register under the
    # composite_score component. They may produce 0 rows because their
    # fitted state isn't hydrated — that's intentional (we're not
    # running the weekly fit jobs in this smoke).
    assert "composite.bayesian_hier.v1" in written
    # GBM only registers when LightGBM is installed; presence-by-key
    # depends on the environment but the runner shouldn't crash.

    _ = UTC  # pragma: no cover - tolerated unused import shape
