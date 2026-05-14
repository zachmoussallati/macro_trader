"""Integration tests: lineage + freshness persistence."""

from __future__ import annotations

import pytest

from macro_trader.data.freshness import touch_freshness, upsert_freshness_row
from macro_trader.data.lineage import (
    IngestStats,
    create_lineage,
    finalize_lineage,
    record_lineage_failure,
)
from macro_trader.db.models.system import DataFreshness, DataLineage, DataSource
from macro_trader.utils.dates import utcnow


def _seed_source(session, source_id: str = "test_source") -> None:
    session.add(
        DataSource(
            source_id=source_id,
            name="Test",
            base_url="http://example",
            requires_auth=False,
            source_metadata={},
            created_at=utcnow(),
        )
    )
    session.flush()


@pytest.mark.integration
def test_lineage_round_trip(db_session) -> None:
    _seed_source(db_session)
    lineage = create_lineage(db_session, source_id="test_source", fetch_method="api")
    stats = IngestStats()
    stats.rows_ingested = 42
    stats.rows_updated = 3
    finalize_lineage(db_session, lineage, stats)
    db_session.flush()

    row = db_session.get(DataLineage, lineage.lineage_id)
    assert row is not None
    assert row.rows_ingested == 42
    assert row.rows_updated == 3
    assert row.error_count == 0


@pytest.mark.integration
def test_lineage_failure_records_metadata(db_session) -> None:
    _seed_source(db_session)
    lineage = create_lineage(db_session, source_id="test_source")
    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        record_lineage_failure(db_session, lineage, exc)
    db_session.flush()
    row = db_session.get(DataLineage, lineage.lineage_id)
    assert row is not None
    assert row.error_count == 1
    assert row.lineage_metadata["error_type"] == "RuntimeError"
    assert "boom" in row.lineage_metadata["error_message"]


@pytest.mark.integration
def test_freshness_touch_resets_failures_on_success(db_session) -> None:
    _seed_source(db_session)
    upsert_freshness_row(
        db_session,
        source_id="test_source",
        series_or_table="test_source.x",
        expected_frequency="daily",
    )
    touch_freshness(
        db_session,
        source_id="test_source",
        series_or_table="test_source.x",
        success=False,
    )
    touch_freshness(
        db_session,
        source_id="test_source",
        series_or_table="test_source.x",
        success=False,
    )
    db_session.flush()
    row = db_session.get(DataFreshness, ("test_source", "test_source.x"))
    assert row is not None
    assert row.consecutive_failures == 2

    touch_freshness(
        db_session,
        source_id="test_source",
        series_or_table="test_source.x",
        success=True,
    )
    db_session.flush()
    row = db_session.get(DataFreshness, ("test_source", "test_source.x"))
    assert row is not None
    assert row.consecutive_failures == 0
    assert row.is_stale is False
