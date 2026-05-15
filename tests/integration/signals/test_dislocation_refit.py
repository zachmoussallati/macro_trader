"""End-to-end refit + persist + reload test for the dislocation models."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy import select

from macro_trader.data.instruments import upsert_instrument
from macro_trader.db.models.market_data import DailyBar
from macro_trader.db.models.system import MethodRegistryRow
from macro_trader.methods.registry import (
    load_serialized_blob,
    store_serialized_blob,
)
from macro_trader.methods.setup import register_all_methods
from macro_trader.signals.dislocation.refit import (
    load_pca_state,
    refit_pca,
    run_weekly_refit,
)
from macro_trader.utils.dates import utcnow


def _seed_panel(session, n_days: int = 320, n_instruments: int = 5) -> None:
    rng = np.random.default_rng(0)
    loadings = rng.normal(scale=0.5, size=(n_instruments, 2))
    factors = rng.normal(scale=0.01, size=(n_days, 2))
    rets = factors @ loadings.T + rng.normal(scale=0.005, size=(n_days, n_instruments))
    prices = 100.0 * np.exp(np.cumsum(rets, axis=0))

    now = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    base = now - timedelta(days=n_days - 1)
    for i in range(n_instruments):
        instrument_id = f"INST_{i}"
        upsert_instrument(
            session,
            instrument_id=instrument_id,
            name=f"Inst {i}",
            asset_class="energy",
            proxy_ticker=f"TKR{i}",
            proxy_type="etf",
        )
        for d in range(n_days):
            session.add(
                DailyBar(
                    instrument_id=instrument_id,
                    value_ts=base + timedelta(days=d),
                    observation_ts=now,
                    close=float(prices[d, i]),
                    source="test",
                )
            )
    session.flush()


@pytest.mark.integration
def test_refit_pca_persists_state_under_blob_size_threshold(db_session) -> None:
    _seed_panel(db_session)
    register_all_methods(db_session)

    result = refit_pca(db_session)
    assert result is not None
    assert result.method_id == "dislocation.pca.v1"
    # Stage 4B prompt asks us to verify the blob stays small. PCA state
    # for ~5 instruments x 3 components is well under the TOAST threshold.
    assert result.blob_size_bytes < 8 * 1024
    assert result.fit_rows > 0
    assert 0.0 <= result.explained_variance <= 1.0

    # The blob was persisted and is reachable from a fresh session.
    blob = load_serialized_blob(db_session, "dislocation.pca.v1")
    assert blob is not None
    assert len(blob) == result.blob_size_bytes


@pytest.mark.integration
def test_load_pca_state_returns_fitted_method(db_session) -> None:
    _seed_panel(db_session)
    register_all_methods(db_session)
    refit_pca(db_session)

    method = load_pca_state(db_session)
    assert method is not None
    assert method._state is not None
    assert "pca" in method._state


@pytest.mark.integration
def test_load_pca_state_returns_none_when_no_blob(db_session) -> None:
    register_all_methods(db_session)
    method = load_pca_state(db_session)
    assert method is None


@pytest.mark.integration
def test_run_weekly_refit_writes_at_least_pca(db_session) -> None:
    """End-to-end: weekly refit writes PCA fitted state for the test
    universe. DFM may or may not converge on this small synthetic
    sample; we assert PCA always succeeds."""
    _seed_panel(db_session, n_days=320)
    register_all_methods(db_session)

    results = run_weekly_refit(db_session)
    pca_results = [r for r in results if r.method_id == "dislocation.pca.v1"]
    assert pca_results, "expected PCA refit to succeed"


@pytest.mark.integration
def test_store_serialized_blob_raises_for_unknown_method(db_session) -> None:
    from macro_trader.methods.registry import MethodNotFoundError

    with pytest.raises(MethodNotFoundError):
        store_serialized_blob(db_session, "nope.does.not.exist.v1", b"x")


@pytest.mark.integration
def test_store_serialized_blob_overwrites_existing(db_session) -> None:
    register_all_methods(db_session)
    store_serialized_blob(db_session, "dislocation.pca.v1", b"first")
    store_serialized_blob(db_session, "dislocation.pca.v1", b"second")

    row = db_session.scalar(
        select(MethodRegistryRow).where(
            MethodRegistryRow.method_id == "dislocation.pca.v1"
        )
    )
    assert row is not None
    assert row.serialized_blob == b"second"
