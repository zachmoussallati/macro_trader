"""FRED ingester unit tests — five scenarios.

We exercise the FRED ingester end-to-end through ``run()`` with the
external ``fredapi.Fred`` class swapped for :class:`tests.fixtures.http.FakeFred`
and the lineage / freshness / heartbeat side-effects captured by the
``patch_base_helpers`` fixture.

The FRED ingester catches per-series exceptions in its fetch loop, so a
total upstream failure manifests as a successful run with zero rows
ingested. These tests assert on that behaviour explicitly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from macro_trader.data.ingestion.fred import FREDIngester
from macro_trader.data.ingestion.fred_series import FRED_SERIES
from tests.fixtures.http import FakeFred, fred_releases_df
from tests.unit.data.ingestion.conftest import CallLog


def _install_fake_fred(monkeypatch: pytest.MonkeyPatch, fake: FakeFred) -> None:
    """Make ``from fredapi import Fred`` evaluate to a constructor that
    returns the provided fake instance regardless of kwargs."""
    monkeypatch.setattr("fredapi.Fred", lambda **_kwargs: fake)


def _build(
    factory: Any, settings: Any, *, max_series: int = 1
) -> tuple[FREDIngester, str]:
    """Build the ingester restricted to the first ``max_series`` series.

    Returns the ingester and the series_id at index 0 — most tests want
    to inject a response for it explicitly.
    """
    ingester = FREDIngester(
        session_factory=factory,
        settings=settings,
        api_key="test_key",
        max_series=max_series,
    )
    first_series_id = FRED_SERIES[0][0]
    return ingester, first_series_id


@pytest.mark.unit
def test_fred_happy_path(
    mock_session_factory: Any,
    mock_session: MagicMock,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Realistic payload returns rows and full success side-effects."""
    ingester, series_id = _build(mock_session_factory, fake_settings)
    fake = FakeFred(responses={series_id: fred_releases_df(n_dates=3, n_vintages=2)})
    _install_fake_fred(monkeypatch, fake)

    stats = ingester.run()

    # 3 dates x 2 vintages = 6 rows ingested.
    assert stats.rows_ingested == 6
    # Lineage created + finalized; failure path not taken; freshness=success.
    assert len(patch_base_helpers.create_lineage) == 1
    assert len(patch_base_helpers.finalize_lineage) == 1
    assert len(patch_base_helpers.record_lineage_failure) == 0
    assert patch_base_helpers.touch_freshness == [
        {
            "source_id": "fred",
            "series_or_table": "fred.series_observations",
            "success": True,
            "expected_frequency": "daily",
        }
    ]
    # The ingester adds at least one Series row + a HeartbeatRow via session.add.
    assert any(type(r).__name__ == "HeartbeatRow" for r in mock_session.added_rows)
    assert any(type(r).__name__ == "Series" for r in mock_session.added_rows)


@pytest.mark.unit
def test_fred_empty_response(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty DataFrame from FRED -> 0 rows ingested, run still succeeds."""
    ingester, series_id = _build(mock_session_factory, fake_settings)
    fake = FakeFred(responses={series_id: fred_releases_df(empty=True)})
    _install_fake_fred(monkeypatch, fake)

    stats = ingester.run()

    assert stats.rows_ingested == 0
    assert len(patch_base_helpers.finalize_lineage) == 1
    assert len(patch_base_helpers.record_lineage_failure) == 0
    assert patch_base_helpers.touch_freshness[-1]["success"] is True


@pytest.mark.unit
def test_fred_rate_limit_per_series_failure(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429-equivalent: tenacity retries 3x then the per-series try/except
    swallows the error. Run completes successfully with 0 rows."""
    ingester, series_id = _build(mock_session_factory, fake_settings)
    fake = FakeFred(responses={series_id: RuntimeError("HTTP 429 Too Many Requests")})
    _install_fake_fred(monkeypatch, fake)

    stats = ingester.run()

    assert stats.rows_ingested == 0
    # Tenacity attempts the call 3 times before giving up.
    assert len(fake.calls) == 3
    # Outer run() succeeded — no failure recorded.
    assert len(patch_base_helpers.record_lineage_failure) == 0
    assert patch_base_helpers.touch_freshness[-1]["success"] is True


@pytest.mark.unit
def test_fred_malformed_payload(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing ``value`` column -> KeyError caught by per-series try/except."""
    ingester, series_id = _build(mock_session_factory, fake_settings)
    fake = FakeFred(responses={series_id: fred_releases_df(malformed=True)})
    _install_fake_fred(monkeypatch, fake)

    stats = ingester.run()

    assert stats.rows_ingested == 0
    assert len(patch_base_helpers.finalize_lineage) == 1
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_fred_retry_exhaustion_does_not_propagate(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All 3 attempts fail; FRED's per-series try/except keeps the run
    successful. Note: the staleness sweep job (Stage 9) is what ultimately
    surfaces a long-running 0-row condition; this ingester's framework
    contract is "if I returned, I succeeded"."""
    ingester, series_id = _build(mock_session_factory, fake_settings)
    fake = FakeFred(responses={series_id: ConnectionError("upstream down")})
    _install_fake_fred(monkeypatch, fake)

    ingester.run()

    assert len(fake.calls) == 3
    # Run did NOT take the failure branch.
    assert len(patch_base_helpers.record_lineage_failure) == 0
    # Freshness recorded a successful sweep (consistent with framework contract).
    assert patch_base_helpers.touch_freshness[-1]["success"] is True
