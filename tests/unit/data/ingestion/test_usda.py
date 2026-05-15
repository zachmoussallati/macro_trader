"""USDA Quick Stats ingester unit tests — five scenarios."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from macro_trader.data.ingestion.usda import QUERIES, USDAIngester
from tests.fixtures.http import usda_quickstats_response
from tests.unit.data.ingestion.conftest import CallLog

_USDA_URL = "https://quickstats.nass.usda.gov/api/api_GET"


@pytest.mark.unit
def test_usda_happy_path(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    n_rows = 4
    with respx.mock(assert_all_called=False) as router:
        router.get(_USDA_URL).mock(
            return_value=httpx.Response(200, json=usda_quickstats_response(n_rows=n_rows))
        )

        ingester = USDAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    # 6 queries x 4 rows.
    assert stats.rows_ingested == len(QUERIES) * n_rows
    assert patch_base_helpers.touch_freshness[-1]["success"] is True


@pytest.mark.unit
def test_usda_empty_response(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(_USDA_URL).mock(
            return_value=httpx.Response(200, json=usda_quickstats_response(empty=True))
        )

        ingester = USDAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    assert stats.rows_ingested == 0
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_usda_rate_limit(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(_USDA_URL).mock(return_value=httpx.Response(429))

        ingester = USDAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    assert stats.rows_ingested == 0
    # 3 retries x 6 queries = 18 calls.
    assert route.call_count == len(QUERIES) * 3
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_usda_malformed_payload(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    """``data`` is a non-list (a string) — slips past USDA's
    ``body.get("data") or []`` fallback, then transform iterates over its
    characters and raises ``AttributeError`` on ``str.get``. ``run()``
    catches the exception, records a lineage failure, marks freshness
    failure, and re-raises (per the ingester framework contract)."""
    with respx.mock(assert_all_called=False) as router:
        router.get(_USDA_URL).mock(
            return_value=httpx.Response(200, json=usda_quickstats_response(malformed=True))
        )

        ingester = USDAIngester(session_factory=mock_session_factory, settings=fake_settings)
        with pytest.raises(AttributeError):
            ingester.run()

    assert len(patch_base_helpers.record_lineage_failure) == 1
    assert patch_base_helpers.touch_freshness[-1]["success"] is False


@pytest.mark.unit
def test_usda_retry_exhaustion(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(_USDA_URL).mock(return_value=httpx.Response(503))

        ingester = USDAIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run()

    assert stats.rows_ingested == 0
    assert route.call_count == len(QUERIES) * 3
    assert patch_base_helpers.touch_freshness[-1]["success"] is True
