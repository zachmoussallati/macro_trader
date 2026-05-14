"""Lineage record creation + persistence helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from macro_trader.db.models.system import DataLineage
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class IngestStats:
    """Summary of one ingester run, kept in-memory before persistence."""

    rows_ingested: int = 0
    rows_updated: int = 0
    rows_rejected: int = 0
    error_count: int = 0
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class LineageRecord:
    """In-memory mirror of a ``system.data_lineage`` row.

    Created at the start of an ingester run; finalised (with stats) at the
    end. ``lineage_id`` is generated up-front so individual data rows can
    reference it.
    """

    lineage_id: uuid.UUID
    source_id: str
    fetched_at: datetime
    fetch_method: str | None = None
    source_version: str | None = None
    transformation_version: str | None = None
    dagster_run_id: str | None = None
    dagster_asset_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def create_lineage(
    session: Session,
    *,
    source_id: str,
    fetch_method: str | None = None,
    source_version: str | None = None,
    transformation_version: str | None = None,
    dagster_run_id: str | None = None,
    dagster_asset_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> LineageRecord:
    """Persist a fresh ``data_lineage`` row and return its in-memory mirror.

    The row starts with zero stats; call :func:`finalize_lineage` after the
    ingest run to write the final counts.
    """
    record = LineageRecord(
        lineage_id=uuid.uuid4(),
        source_id=source_id,
        fetched_at=utcnow(),
        fetch_method=fetch_method,
        source_version=source_version,
        transformation_version=transformation_version,
        dagster_run_id=dagster_run_id,
        dagster_asset_key=dagster_asset_key,
        metadata=dict(metadata or {}),
    )
    row = DataLineage(
        lineage_id=record.lineage_id,
        source_id=record.source_id,
        fetched_at=record.fetched_at,
        fetch_method=record.fetch_method,
        source_version=record.source_version,
        transformation_version=record.transformation_version,
        rows_ingested=0,
        rows_updated=0,
        rows_rejected=0,
        error_count=0,
        lineage_metadata=record.metadata,
        dagster_run_id=record.dagster_run_id,
        dagster_asset_key=record.dagster_asset_key,
    )
    session.add(row)
    session.flush()
    return record


def finalize_lineage(
    session: Session,
    lineage: LineageRecord,
    stats: IngestStats,
) -> None:
    """Write final stats back to the persisted ``data_lineage`` row."""
    row = session.get(DataLineage, lineage.lineage_id)
    if row is None:
        return
    row.rows_ingested = stats.rows_ingested
    row.rows_updated = stats.rows_updated
    row.rows_rejected = stats.rows_rejected
    row.error_count = stats.error_count
    if stats.notes:
        merged = dict(row.lineage_metadata or {})
        merged.update(stats.notes)
        row.lineage_metadata = merged
    session.flush()


def record_lineage_failure(
    session: Session,
    lineage: LineageRecord,
    error: BaseException,
) -> None:
    """Mark a lineage run as failed and record the exception type/message."""
    row = session.get(DataLineage, lineage.lineage_id)
    if row is None:
        return
    row.error_count = (row.error_count or 0) + 1
    merged = dict(row.lineage_metadata or {})
    merged["error_type"] = type(error).__name__
    merged["error_message"] = str(error)[:1024]
    row.lineage_metadata = merged
    session.flush()
