"""NOAA Climate Data Online ingester unit tests — five scenarios."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from macro_trader.data.ingestion.noaa import DATATYPES, STATIONS, NOAAIngester
from tests.fixtures.http import noaa_gsom_response
from tests.unit.data.ingestion.conftest import CallLog

_NOAA_URL = "https://www.ncei.noaa.gov/cdo-web/api/v2/data"

# 4 stations x 2 datatypes = 8 calls per ingest run.
_TOTAL_CALLS_PER_RUN = len(STATIONS) * len(DATATYPES)


@pytest.mark.unit
def test_noaa_happy_path(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    n_rows = 3
    with respx.mock(assert_all_called=False) as router:
        router.get(_NOAA_URL).mock(
            return_value=httpx.Response(200, json=noaa_gsom_response(n_rows=n_rows))
        )

        ingester = NOAAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    # Each call returns n_rows; 8 calls total.
    assert stats.rows_ingested == _TOTAL_CALLS_PER_RUN * n_rows
    assert patch_base_helpers.touch_freshness[-1] == {
        "source_id": "noaa",
        "series_or_table": "alt_data.weather_data",
        "success": True,
        "expected_frequency": "daily",
    }


@pytest.mark.unit
def test_noaa_empty_response(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(_NOAA_URL).mock(
            return_value=httpx.Response(200, json=noaa_gsom_response(empty=True))
        )

        ingester = NOAAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    assert stats.rows_ingested == 0


@pytest.mark.unit
def test_noaa_rate_limit(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(_NOAA_URL).mock(return_value=httpx.Response(429))

        ingester = NOAAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    assert stats.rows_ingested == 0
    # 3 retries x 8 chunks.
    assert route.call_count == _TOTAL_CALLS_PER_RUN * 3
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_noaa_malformed_payload(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    """Date in unparseable format -> transform's per-row try/except skips
    and the row is silently dropped."""
    with respx.mock(assert_all_called=False) as router:
        router.get(_NOAA_URL).mock(
            return_value=httpx.Response(200, json=noaa_gsom_response(malformed=True))
        )

        ingester = NOAAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    assert stats.rows_ingested == 0


@pytest.mark.unit
def test_noaa_retry_exhaustion(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(_NOAA_URL).mock(return_value=httpx.Response(503))

        ingester = NOAAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    assert stats.rows_ingested == 0
    assert route.call_count == _TOTAL_CALLS_PER_RUN * 3
    assert patch_base_helpers.touch_freshness[-1]["success"] is True
