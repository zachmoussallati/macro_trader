"""Unit tests for the Ingester base class behaviour."""

from __future__ import annotations

import pytest

from macro_trader.data.ingestion.base import Ingester
from macro_trader.data.lineage import IngestStats, LineageRecord


class _FakeIngester(Ingester[dict]):
    source_id = "test_source"
    series_or_table = "test_source.fake"
    expected_frequency = "daily"
    fetch_method = "fake"

    def fetch(self, *, since=None):
        return {"rows": ["a", "b"]}

    def transform(self, raw):
        return [{"value": v} for v in raw["rows"]]

    def persist(self, session, rows, lineage):
        stats = IngestStats()
        stats.rows_ingested = len(rows)
        return stats


@pytest.mark.unit
def test_ingester_requires_source_id() -> None:
    class BadIngester(Ingester[dict]):
        source_id = ""

        def fetch(self, *, since=None):
            return {}

        def transform(self, raw):
            return []

        def persist(self, session, rows, lineage):
            return IngestStats()

    with pytest.raises(ValueError):
        BadIngester(session_factory=None, settings=None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_lineage_record_dataclass() -> None:
    """Sanity: LineageRecord constructs and round-trips fields."""
    import uuid

    from macro_trader.utils.dates import utcnow

    rec = LineageRecord(
        lineage_id=uuid.uuid4(),
        source_id="x",
        fetched_at=utcnow(),
        fetch_method="api",
    )
    assert rec.source_id == "x"
    assert rec.fetch_method == "api"
