"""CFTC COT ingester unit tests — five scenarios.

CFTC pulls weekly ZIPs from cftc.gov for three report types
(disaggregated, legacy, financial_tff) per year. Stage 3's year-boundary
fix adds a prior-year fallback in early January, so on a non-boundary
date there is exactly one year x three reports = 3 download URLs.

Per-(year, report_type) failures are caught by the fetch loop's
try/except — same pattern as FRED.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from macro_trader.data.ingestion.cftc import CFTCIngester
from tests.fixtures.http import cftc_zip_bytes, cftc_zip_no_txt_bytes
from tests.unit.data.ingestion.conftest import CallLog

# A non-boundary "now" so _years_to_fetch returns just the current year.
_TEST_NOW = datetime(2025, 6, 15)


_DISAGG_URL = "https://www.cftc.gov/files/dea/history/com_disagg_txt_2025.zip"
_LEGACY_URL = "https://www.cftc.gov/files/dea/history/deacot2025.zip"
_TFF_URL = "https://www.cftc.gov/files/dea/history/fut_fin_txt_2025.zip"


def _route_all_ok(router: respx.MockRouter) -> None:
    """Mock all three CFTC URLs with valid (small) ZIP payloads."""
    router.get(_DISAGG_URL).mock(
        return_value=httpx.Response(200, content=cftc_zip_bytes(report_type="disaggregated"))
    )
    router.get(_LEGACY_URL).mock(
        return_value=httpx.Response(200, content=cftc_zip_bytes(report_type="legacy"))
    )
    router.get(_TFF_URL).mock(
        return_value=httpx.Response(200, content=cftc_zip_bytes(report_type="financial_tff"))
    )


@pytest.mark.unit
def test_cftc_happy_path(
    mock_session_factory: Any,
    mock_session: MagicMock,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    with respx.mock(assert_all_called=False) as router:
        _route_all_ok(router)
        ingester = CFTCIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run(since=_TEST_NOW)

    # Each ZIP has 2 instrument rows x 3 report types = 6 rows.
    assert stats.rows_ingested == 6
    assert len(patch_base_helpers.create_lineage) == 1
    assert len(patch_base_helpers.finalize_lineage) == 1
    assert patch_base_helpers.touch_freshness[-1] == {
        "source_id": "cftc",
        "series_or_table": "positioning.cot_weekly",
        "success": True,
        "expected_frequency": "weekly",
    }


@pytest.mark.unit
def test_cftc_empty_response(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    """All three reports return empty (header-only) ZIPs -> 0 rows."""
    with respx.mock(assert_all_called=False) as router:
        for url, rt in [
            (_DISAGG_URL, "disaggregated"),
            (_LEGACY_URL, "legacy"),
            (_TFF_URL, "financial_tff"),
        ]:
            router.get(url).mock(
                return_value=httpx.Response(200, content=cftc_zip_bytes(report_type=rt, empty=True))
            )

        ingester = CFTCIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run(since=_TEST_NOW)

    assert stats.rows_ingested == 0
    assert len(patch_base_helpers.record_lineage_failure) == 0
    assert patch_base_helpers.touch_freshness[-1]["success"] is True


@pytest.mark.unit
def test_cftc_rate_limit_swallowed_per_report(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    """Disaggregated returns 429 every time; legacy + TFF succeed.

    Tenacity retries the failing URL 3x; the per-report try/except
    swallows the final HTTPStatusError. The other two reports still
    persist their rows.
    """
    with respx.mock(assert_all_called=False) as router:
        disagg_route = router.get(_DISAGG_URL).mock(return_value=httpx.Response(429))
        router.get(_LEGACY_URL).mock(
            return_value=httpx.Response(200, content=cftc_zip_bytes(report_type="legacy"))
        )
        router.get(_TFF_URL).mock(
            return_value=httpx.Response(200, content=cftc_zip_bytes(report_type="financial_tff"))
        )

        ingester = CFTCIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run(since=_TEST_NOW)

    # 2 instruments x 2 successful reports = 4 rows (disagg dropped).
    assert stats.rows_ingested == 4
    # 3 retry attempts on the failing URL.
    assert disagg_route.call_count == 3
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_cftc_malformed_payload(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    """Malformed CSV: rows with empty date strings are dropped during
    transform's strptime. Run still completes."""
    with respx.mock(assert_all_called=False) as router:
        router.get(_DISAGG_URL).mock(
            return_value=httpx.Response(
                200, content=cftc_zip_bytes(report_type="disaggregated", malformed=True)
            )
        )
        # The other two URLs hit but get a "no .txt" zip -> empty parse.
        router.get(_LEGACY_URL).mock(
            return_value=httpx.Response(200, content=cftc_zip_no_txt_bytes())
        )
        router.get(_TFF_URL).mock(
            return_value=httpx.Response(200, content=cftc_zip_no_txt_bytes())
        )

        ingester = CFTCIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run(since=_TEST_NOW)

    assert stats.rows_ingested == 0
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_cftc_retry_exhaustion(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
) -> None:
    """All three URLs return 503 every time. Retries x 3 each. Per-report
    try/except keeps the run successful with 0 rows."""
    with respx.mock(assert_all_called=False) as router:
        routes = [
            router.get(url).mock(return_value=httpx.Response(503))
            for url in (_DISAGG_URL, _LEGACY_URL, _TFF_URL)
        ]

        ingester = CFTCIngester(session_factory=mock_session_factory, settings=fake_settings)
        stats = ingester.run(since=_TEST_NOW)

    assert stats.rows_ingested == 0
    for r in routes:
        assert r.call_count == 3
    assert len(patch_base_helpers.record_lineage_failure) == 0
    assert patch_base_helpers.touch_freshness[-1]["success"] is True
