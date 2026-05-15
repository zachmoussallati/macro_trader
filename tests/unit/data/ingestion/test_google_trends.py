"""Google Trends (pytrends) ingester unit tests — five scenarios.

The pytrends library is patched at the import boundary: we replace
``pytrends.request.TrendReq`` with :class:`tests.fixtures.http.FakePytrends`.
Pytrends does not have its own retry decorator inside the ingester; the
fetch loop's own try/except + ``time.sleep(5)`` provides a single layer
of resilience per query.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any

import pytest

from macro_trader.data.ingestion.google_trends import (
    QUERY_TO_INSTRUMENTS,
    GoogleTrendsIngester,
)
from tests.fixtures.http import FakePytrends, pytrends_interest_df
from tests.unit.data.ingestion.conftest import CallLog


def _install_fake_pytrends(
    monkeypatch: pytest.MonkeyPatch, fake_factory: Any
) -> None:
    """Patch ``pytrends.request.TrendReq`` so the ingester's local import
    picks up our fake.

    ``fake_factory`` is a zero-arg callable returning a fresh
    :class:`FakePytrends`. The ingester instantiates ``TrendReq(...)``
    once per ``fetch`` call.
    """
    fake_module = ModuleType("pytrends.request")
    fake_module.TrendReq = lambda **_kwargs: fake_factory()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pytrends.request", fake_module)


@pytest.mark.unit
def test_google_trends_happy_path(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = ["oil price"]
    fake = FakePytrends(
        responses={"oil price": pytrends_interest_df(query="oil price", n_rows=4)}
    )
    _install_fake_pytrends(monkeypatch, lambda: fake)

    ingester = GoogleTrendsIngester(
        session_factory=mock_session_factory,
        settings=fake_settings,
        queries=queries,
    )
    stats = ingester.run()

    assert stats.rows_ingested == 4
    assert fake.build_calls == [["oil price"]]
    assert fake.interest_calls == 1
    assert patch_base_helpers.touch_freshness[-1]["success"] is True


@pytest.mark.unit
def test_google_trends_empty_response(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = ["recession"]
    fake = FakePytrends(responses={"recession": pytrends_interest_df(empty=True)})
    _install_fake_pytrends(monkeypatch, lambda: fake)

    ingester = GoogleTrendsIngester(
        session_factory=mock_session_factory,
        settings=fake_settings,
        queries=queries,
    )
    stats = ingester.run()

    assert stats.rows_ingested == 0
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_google_trends_rate_limit(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_payload`` raises (pytrends-style ResponseError on 429).

    The ingester's per-query try/except catches it, sleeps 5s
    (no-op'd by autouse fixture), and continues. Run completes."""
    queries = ["oil price", "buy gold"]
    fake = FakePytrends(
        responses={"buy gold": pytrends_interest_df(query="buy gold", n_rows=2)},
        build_payload_error=RuntimeError("HTTP 429 Too Many Requests"),
    )
    # First query fails on build_payload; reset error after — we'll do
    # this by giving the second invocation a different fake.
    fake_seq: list[FakePytrends] = [
        fake,
        FakePytrends(
            responses={"buy gold": pytrends_interest_df(query="buy gold", n_rows=2)}
        ),
    ]
    # The ingester only constructs TrendReq once per fetch run, then loops
    # all queries on that one instance. So we hand out the SAME instance
    # always; the build_payload error only triggers the first time.
    fake.build_payload_error = None  # second call onward succeeds
    fake.responses["oil price"] = pytrends_interest_df(query="oil price", n_rows=3)
    # We need build_payload to fail ONCE then succeed. Override its method.
    real_build = FakePytrends.build_payload
    call_state = {"count": 0}

    def _flaky_build(self: FakePytrends, kw_list: list[str], **kwargs: Any) -> None:
        call_state["count"] += 1
        if call_state["count"] == 1:
            raise RuntimeError("HTTP 429 Too Many Requests")
        real_build(self, kw_list, **kwargs)

    monkeypatch.setattr(FakePytrends, "build_payload", _flaky_build)
    _install_fake_pytrends(monkeypatch, lambda: fake)
    _ = fake_seq  # silence unused

    ingester = GoogleTrendsIngester(
        session_factory=mock_session_factory,
        settings=fake_settings,
        queries=queries,
    )
    stats = ingester.run()

    # Only the second query persisted: 2 rows.
    assert stats.rows_ingested == 2
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_google_trends_malformed_payload(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DataFrame returned has no DatetimeIndex → ``ts.to_pydatetime()``
    raises inside the iter-rows loop, which sits OUTSIDE the per-query
    try/except. Run wrapper catches, lineage records failure, freshness
    flagged failed, exception re-raised. (Existing google_trends.py
    behaviour; tightening the wrap is a Stage 4B refactor noted in
    ``next.md``.)"""
    queries = ["inflation"]
    fake = FakePytrends(responses={"inflation": pytrends_interest_df(malformed=True)})
    _install_fake_pytrends(monkeypatch, lambda: fake)

    ingester = GoogleTrendsIngester(
        session_factory=mock_session_factory,
        settings=fake_settings,
        queries=queries,
    )
    with pytest.raises(AttributeError):
        ingester.run()

    assert len(patch_base_helpers.record_lineage_failure) == 1
    assert patch_base_helpers.touch_freshness[-1]["success"] is False


@pytest.mark.unit
def test_google_trends_retry_exhaustion_all_queries_fail(
    mock_session_factory: Any,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_payload raises on every query → 0 rows, run completes.

    Pytrends has no tenacity wrapper, so this is one attempt per query
    rather than three retries. We assert each query was at least built
    against once."""
    queries = list(QUERY_TO_INSTRUMENTS.keys())[:3]
    fake = FakePytrends(build_payload_error=ConnectionError("upstream down"))
    _install_fake_pytrends(monkeypatch, lambda: fake)

    ingester = GoogleTrendsIngester(
        session_factory=mock_session_factory,
        settings=fake_settings,
        queries=queries,
    )
    stats = ingester.run()

    assert stats.rows_ingested == 0
    assert len(fake.build_calls) == len(queries)
    assert len(patch_base_helpers.record_lineage_failure) == 0
    assert patch_base_helpers.touch_freshness[-1]["success"] is True
