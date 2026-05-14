"""Ingester base class.

Every per-source ingester subclasses :class:`Ingester` and implements three
hooks:

- ``fetch(since)`` — pull from the source. Returns whatever data shape the
  source returns; the ``transform`` step normalises it.
- ``transform(raw)`` — convert into a list of normalised dicts. The shape
  depends on the destination table; ``persist`` is the place that knows.
- ``persist(rows, lineage)`` — UPSERT into the destination tables.

The base class wraps these in :meth:`run`, which:

1. Creates a ``data_lineage`` row.
2. Calls fetch → transform → persist.
3. Writes a row to ``system.heartbeat`` (``source = "ingest.<name>"``).
4. Updates ``system.data_freshness``.
5. Logs at info or error level with structlog context bound.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any, Generic, TypeVar

import structlog

from macro_trader.data.freshness import touch_freshness
from macro_trader.data.lineage import (
    IngestStats,
    LineageRecord,
    create_lineage,
    finalize_lineage,
    record_lineage_failure,
)
from macro_trader.db.models.system import HeartbeatRow
from macro_trader.logging_setup import get_logger
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker


# Source-specific raw container. Use Any here; concrete subclasses bind their
# own structures (e.g. pd.DataFrame, dict, list[dict]).
RawData = TypeVar("RawData")


class Ingester(ABC, Generic[RawData]):
    """Abstract base for per-source ingesters.

    Attributes:
        source_id: matches the ``source_id`` in ``system.data_sources``.
        series_or_table: identifier passed to freshness tracking. Default
            uses ``source_id`` but can be overridden per-series.
        expected_frequency: ``daily`` / ``weekly`` / ``monthly`` /
            ``quarterly``. Drives staleness detection.
    """

    source_id: str = ""
    series_or_table: str = ""
    expected_frequency: str = "daily"
    fetch_method: str = "api"

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Any,
    ) -> None:
        if not self.source_id:
            raise ValueError(f"{type(self).__name__} must set class attr `source_id`")
        self.session_factory = session_factory
        self.settings = settings
        self.log = get_logger(f"ingest.{self.source_id}")

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    @abstractmethod
    def fetch(self, *, since: datetime | None = None) -> RawData:
        """Pull raw data from the source. Must be idempotent in side-effects."""

    @abstractmethod
    def transform(self, raw: RawData) -> list[dict[str, Any]]:
        """Normalise raw data into a list of dicts ready to persist."""

    @abstractmethod
    def persist(
        self,
        session: Session,
        rows: list[dict[str, Any]],
        lineage: LineageRecord,
    ) -> IngestStats:
        """UPSERT rows. Returns a final :class:`IngestStats`."""

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def run(
        self,
        *,
        since: datetime | None = None,
        dagster_run_id: str | None = None,
        dagster_asset_key: str | None = None,
    ) -> IngestStats:
        """End-to-end ingest with lineage, freshness, and heartbeat."""
        series_or_table = self.series_or_table or self.source_id
        with structlog.contextvars.bound_contextvars(
            source=self.source_id,
            asset_key=dagster_asset_key or f"ingest.{self.source_id}",
            run_id=dagster_run_id,
        ):
            self.log.info("ingest.start")
            with self.session_factory() as session:
                lineage = create_lineage(
                    session,
                    source_id=self.source_id,
                    fetch_method=self.fetch_method,
                    dagster_run_id=dagster_run_id,
                    dagster_asset_key=dagster_asset_key,
                )
                session.commit()  # make lineage row visible if fetch fails
            try:
                raw = self.fetch(since=since)
                rows = self.transform(raw)
                with self.session_factory() as session:
                    stats = self.persist(session, rows, lineage)
                    finalize_lineage(session, lineage, stats)
                    self._write_heartbeat(session, stats)
                    touch_freshness(
                        session,
                        source_id=self.source_id,
                        series_or_table=series_or_table,
                        success=True,
                        expected_frequency=self.expected_frequency,
                    )
                    session.commit()
                self.log.info(
                    "ingest.success",
                    rows_ingested=stats.rows_ingested,
                    rows_updated=stats.rows_updated,
                    rows_rejected=stats.rows_rejected,
                )
                return stats
            except Exception as exc:
                with self.session_factory() as session:
                    record_lineage_failure(session, lineage, exc)
                    touch_freshness(
                        session,
                        source_id=self.source_id,
                        series_or_table=series_or_table,
                        success=False,
                        expected_frequency=self.expected_frequency,
                    )
                    session.commit()
                self.log.error("ingest.failed", error=str(exc), exc_info=True)
                raise

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _write_heartbeat(self, session: Session, stats: IngestStats) -> None:
        session.add(
            HeartbeatRow(
                timestamp=utcnow(),
                source=f"ingest.{self.source_id}",
                meta={
                    "rows_ingested": stats.rows_ingested,
                    "rows_updated": stats.rows_updated,
                    "rows_rejected": stats.rows_rejected,
                },
            )
        )
