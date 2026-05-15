"""EIA ingester unit tests — five scenarios."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from macro_trader.data.ingestion.eia import EIA_SERIES, EIAIngester
from tests.fixtures.http import eia_series_response
from tests.unit.data.ingestion.conftest import CallLog


def _series_url(series_id: str) -> str:
    return f"https://api.eia.gov/v2/seriesid/{series_id}"


def _route_all(router: respx.MockRouter, response_factory: Any) -> list[Any]:
    """Register one route per configured EIA series with the given factory.

    ``response_factory`` is a zero-arg callable returning ``httpx.Response``.
    """
    return [router.get(_series_url(s[0])).mock(side_effect=lambda _req: response_factory()) for s in EIA_SERIES]


@pytest.mark.unit
def test_eia_happy_path(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    n_rows_per_series = 3
    with respx.mock(assert_all_called=False) as router:
        _route_all(
            router,
            lambda: httpx.Response(200, json=eia_series_response(n_rows=n_rows_per_series)),
        )

        ingester = EIAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    # 5 series x 3 rows.
    assert stats.rows_ingested == len(EIA_SERIES) * n_rows_per_series
    assert len(patch_base_helpers.create_lineage) == 1
    assert patch_base_helpers.touch_freshness[-1]["success"] is True


@pytest.mark.unit
def test_eia_empty_response(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        _route_all(router, lambda: httpx.Response(200, json=eia_series_response(empty=True)))

        ingester = EIAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    assert stats.rows_ingested == 0
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_eia_rate_limit_per_series(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    """First series returns 429 on every retry; remaining series succeed."""
    failing_series_id = EIA_SERIES[0][0]
    with respx.mock(assert_all_called=False) as router:
        failing = router.get(_series_url(failing_series_id)).mock(
            return_value=httpx.Response(429)
        )
        for spec in EIA_SERIES[1:]:
            router.get(_series_url(spec[0])).mock(
                return_value=httpx.Response(200, json=eia_series_response(n_rows=2))
            )

        ingester = EIAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    # 4 successful series x 2 rows.
    assert stats.rows_ingested == (len(EIA_SERIES) - 1) * 2
    assert failing.call_count == 3
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_eia_malformed_payload(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    """Response missing the 'response' key -> ingester gracefully returns
    no data for that series."""
    with respx.mock(assert_all_called=False) as router:
        _route_all(
            router, lambda: httpx.Response(200, json=eia_series_response(malformed=True))
        )

        ingester = EIAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    assert stats.rows_ingested == 0
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_eia_retry_exhaustion(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    """All series return 503 every time. 3 retries x 5 series = 15 hits."""
    with respx.mock(assert_all_called=False) as router:
        routes = _route_all(router, lambda: httpx.Response(503))

        ingester = EIAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    assert stats.rows_ingested == 0
    for r in routes:
        assert r.call_count == 3
    assert len(patch_base_helpers.record_lineage_failure) == 0
    assert patch_base_helpers.touch_freshness[-1]["success"] is True
