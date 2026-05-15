"""Shared fixtures for ingester unit tests.

Goal: exercise fetch / transform / persist for each ingester without
needing real Postgres or external APIs. The test pace target is
sub-second per file.

Design:

- ``fake_settings`` — stand-in for ``Settings`` with non-empty API keys.
- ``mock_session`` + ``mock_session_factory`` — a ``MagicMock`` that
  satisfies the ingester's ``with self.session_factory() as session:``
  pattern and lets us assert on ``session.add(...)`` / ``session.execute(...)``.
- ``no_retry_sleep`` (autouse) — kills tenacity's exponential backoff so
  rate-limit tests run instantly.
- ``no_lineage_side_effects`` (autouse) — patches the four DB helpers
  imported into ``ingestion.base`` so ``run()`` doesn't try to write
  lineage / freshness / heartbeat rows. Each call is recorded on the
  ``CallLog`` returned for assertions.

Tests that want richer DB-side assertions can reach for the existing
``db_session`` fixture from ``tests/conftest.py`` and write a separate
integration test; the unit suite here keeps things hermetic.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from macro_trader.data.lineage import LineageRecord
from macro_trader.utils.dates import utcnow


@pytest.fixture
def fake_settings() -> Any:
    """Stand-in for ``macro_trader.config.Settings``."""
    return SimpleNamespace(
        data_sources=SimpleNamespace(
            fred_api_key="test_fred_key",
            eia_api_key="test_eia_key",
            usda_api_key="test_usda_key",
            noaa_api_key="test_noaa_key",
            alpha_vantage_api_key="",
            quandl_api_key="",
        )
    )


def _make_mock_session() -> MagicMock:
    """A ``MagicMock`` Session that supports the patterns ingesters use.

    - ``session.add(row)``           — recorded on ``mock.added_rows``.
    - ``session.execute(stmt)``      — returns a Mock with ``rowcount=0`` by
                                       default; tests can override.
    - ``session.scalars(stmt)``      — returns an empty iterable by default.
    - ``session.flush()`` / ``commit()`` / ``close()`` — no-ops.
    """
    session = MagicMock(name="MockSession")
    session.added_rows = []
    session.add.side_effect = lambda row: session.added_rows.append(row)
    # Mimic a CursorResult with rowcount.
    result_mock = MagicMock(name="MockResult", rowcount=0)
    session.execute.return_value = result_mock
    # session.scalars(...) → iterable; default empty.
    scalars_mock = MagicMock(name="MockScalars")
    scalars_mock.__iter__ = lambda _self: iter([])
    session.scalars.return_value = scalars_mock
    return session


@pytest.fixture
def mock_session() -> MagicMock:
    return _make_mock_session()


@pytest.fixture
def mock_session_factory(mock_session: MagicMock) -> Any:
    """A factory whose returned object is a context manager yielding the
    same ``mock_session`` every time. This matches the ingester's ``with
    self.session_factory() as session:`` usage."""

    @contextmanager
    def _ctx() -> Iterator[MagicMock]:
        yield mock_session

    factory = MagicMock(name="MockSessionFactory")
    factory.side_effect = lambda: _ctx()
    return factory


@dataclass
class CallLog:
    create_lineage: list[dict[str, Any]] = field(default_factory=list)
    finalize_lineage: list[dict[str, Any]] = field(default_factory=list)
    record_lineage_failure: list[dict[str, Any]] = field(default_factory=list)
    touch_freshness: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
def call_log() -> CallLog:
    return CallLog()


@pytest.fixture
def patch_base_helpers(monkeypatch: pytest.MonkeyPatch, call_log: CallLog) -> CallLog:
    """Replace the lineage / freshness helpers imported into ``base`` with
    stubs that just record arguments. The ingester's ``run()`` then runs
    end-to-end without touching Postgres."""

    def _create(session: Any, **kwargs: Any) -> LineageRecord:
        call_log.create_lineage.append(kwargs)
        return LineageRecord(
            lineage_id=uuid.uuid4(),
            source_id=kwargs.get("source_id", "test"),
            fetched_at=utcnow(),
            fetch_method=kwargs.get("fetch_method"),
        )

    def _finalize(session: Any, lineage: LineageRecord, stats: Any) -> None:
        call_log.finalize_lineage.append({"lineage_id": lineage.lineage_id, "stats": stats})

    def _record_failure(session: Any, lineage: LineageRecord, error: BaseException) -> None:
        call_log.record_lineage_failure.append(
            {"lineage_id": lineage.lineage_id, "error": error}
        )

    def _touch(
        session: Any,
        *,
        source_id: str,
        series_or_table: str,
        success: bool,
        expected_frequency: str = "daily",
        expected_delay_seconds: int | None = None,
    ) -> None:
        call_log.touch_freshness.append(
            {
                "source_id": source_id,
                "series_or_table": series_or_table,
                "success": success,
                "expected_frequency": expected_frequency,
            }
        )

    monkeypatch.setattr("macro_trader.data.ingestion.base.create_lineage", _create)
    monkeypatch.setattr("macro_trader.data.ingestion.base.finalize_lineage", _finalize)
    monkeypatch.setattr(
        "macro_trader.data.ingestion.base.record_lineage_failure", _record_failure
    )
    monkeypatch.setattr("macro_trader.data.ingestion.base.touch_freshness", _touch)
    return call_log


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make tenacity's exponential backoff and per-ingester ``time.sleep``
    politeness pauses both no-ops, so tests are fast even when the
    rate-limit / retry-exhaustion paths execute."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    # Tenacity's default sleep strategy ultimately calls time.sleep too,
    # but its own indirection layer is also monkey-patched for
    # belt-and-braces.
    monkeypatch.setattr("tenacity.nap.sleep", lambda *_a, **_k: None)
