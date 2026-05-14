"""FRED + ALFRED ingester.

For each series in :data:`FRED_SERIES`, fetch every vintage via ALFRED so we
preserve point-in-time integrity. Each ``(series_id, value_ts)`` pair can
have many rows, distinguished by ``realtime_start`` / ``realtime_end``.

The first vintage for a (series, value_ts) is flagged ``is_initial = True``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from macro_trader.data.ingestion.base import Ingester
from macro_trader.data.ingestion.fred_series import FRED_SERIES
from macro_trader.data.lineage import IngestStats, LineageRecord
from macro_trader.db.models.macro_data import Series, SeriesObservation
from macro_trader.utils.dates import ensure_aware, utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class FREDRaw:
    """Per-series raw payload from ALFRED."""

    series_rows: list[dict[str, Any]] = field(default_factory=list)


class FREDIngester(Ingester[FREDRaw]):
    """Pulls every vintage of every configured FRED series."""

    source_id = "fred"
    series_or_table = "fred.series_observations"
    expected_frequency = "daily"
    fetch_method = "api"

    def __init__(
        self,
        *,
        session_factory,
        settings,
        api_key: str | None = None,
        max_series: int | None = None,
    ) -> None:
        super().__init__(session_factory=session_factory, settings=settings)
        self.api_key = api_key or settings.data_sources.fred_api_key
        if not self.api_key:
            raise ValueError("FRED_API_KEY missing; set in .env or settings.")
        self.max_series = max_series  # for tests / smoke runs

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    def fetch(self, *, since: datetime | None = None) -> FREDRaw:
        from fredapi import Fred  # local import — heavy dep

        fred = Fred(api_key=self.api_key)
        raw = FREDRaw()
        series_to_fetch = FRED_SERIES if self.max_series is None else FRED_SERIES[: self.max_series]
        cutoff = since.astimezone(UTC) if since else None

        for spec in series_to_fetch:
            series_id = spec[0]
            try:
                series_rows = self._fetch_one(fred, series_id, cutoff=cutoff)
            except Exception as exc:
                self.log.warning("ingest.fred.series_failed", series=series_id, error=str(exc))
                continue
            for row in series_rows:
                row["_spec"] = spec
            raw.series_rows.extend(series_rows)

        return raw

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, max=30),
        reraise=True,
    )
    def _fetch_one(
        self,
        fred: Any,
        series_id: str,
        *,
        cutoff: datetime | None,
    ) -> list[dict[str, Any]]:
        """Fetch every vintage of one series."""
        df = fred.get_series_all_releases(series_id)
        if df is None or df.empty:
            return []

        # ALFRED columns: 'date' (value_ts), 'realtime_start', 'value'.
        # 'date' is per observation; 'realtime_start' is when that vintage
        # was published. 'realtime_end' is the day before the next vintage.
        df = df.sort_values(["date", "realtime_start"])
        # Compute realtime_end as the day before the next vintage for the
        # same date (NaT for the latest vintage of each date).
        df["realtime_end"] = df.groupby("date")["realtime_start"].shift(-1) - timedelta(days=1)

        if cutoff is not None:
            df = df[df["realtime_start"] >= cutoff.replace(tzinfo=None)]

        rows: list[dict[str, Any]] = []
        for _, r in df.iterrows():
            value = r["value"]
            try:
                value = (
                    float(value)
                    if value is not None and not (isinstance(value, float) and value != value)
                    else None
                )
            except (TypeError, ValueError):
                value = None
            rows.append(
                {
                    "series_id": f"FRED:{series_id}",
                    "value_ts": ensure_aware(datetime.combine(r["date"], datetime.min.time())),
                    "realtime_start": ensure_aware(
                        datetime.combine(r["realtime_start"], datetime.min.time())
                    ),
                    "realtime_end": ensure_aware(
                        datetime.combine(r["realtime_end"], datetime.min.time())
                    )
                    if r["realtime_end"] is not None and r["realtime_end"] == r["realtime_end"]
                    else None,
                    "value": value,
                }
            )
        # Be polite to the FRED API.
        time.sleep(0.05)
        return rows

    # ------------------------------------------------------------------
    # Transform
    # ------------------------------------------------------------------
    def transform(self, raw: FREDRaw) -> list[dict[str, Any]]:
        # Group by series and assign revision_number / is_initial. The first
        # vintage (lowest realtime_start) per (series_id, value_ts) is the
        # initial release.
        by_key: dict[tuple[str, datetime], list[dict[str, Any]]] = {}
        for row in raw.series_rows:
            by_key.setdefault((row["series_id"], row["value_ts"]), []).append(row)

        out: list[dict[str, Any]] = []
        for (_series_id, _value_ts), rows in by_key.items():
            rows.sort(key=lambda r: r["realtime_start"])
            for i, row in enumerate(rows):
                spec = row.pop("_spec", None)
                row["observation_ts"] = row["realtime_start"]
                row["is_initial"] = i == 0
                row["revision_number"] = i
                row["source"] = "fred"
                row["source_version"] = None
                row["_spec"] = spec
            out.extend(rows)
        return out

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    def persist(
        self,
        session: Session,
        rows: list[dict[str, Any]],
        lineage: LineageRecord,
    ) -> IngestStats:
        stats = IngestStats()
        if not rows:
            return stats

        self._ensure_series_rows(session, rows)
        chunked: list[dict[str, Any]] = []
        for r in rows:
            r.pop("_spec", None)
            chunked.append({**r, "lineage_id": lineage.lineage_id})

        stmt = pg_insert(SeriesObservation).values(chunked)
        update_cols = {
            "value": stmt.excluded.value,
            "realtime_end": stmt.excluded.realtime_end,
            "is_initial": stmt.excluded.is_initial,
            "revision_number": stmt.excluded.revision_number,
            "source": stmt.excluded.source,
            "lineage_id": stmt.excluded.lineage_id,
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["series_id", "value_ts", "observation_ts"],
            set_=update_cols,
        )
        result = session.execute(stmt)
        # PG doesn't tell us which were inserted vs updated reliably; report
        # total touched as ingested and let the caller infer.
        stats.rows_ingested = result.rowcount or len(chunked)
        return stats

    def _ensure_series_rows(self, session: Session, obs_rows: list[dict[str, Any]]) -> None:
        seen = {row["series_id"] for row in obs_rows}
        existing = set(session.scalars(select(Series.series_id).where(Series.series_id.in_(seen))))
        missing = seen - existing
        if not missing:
            return

        # Build a quick lookup from id → spec so we can fill metadata.
        spec_by_id = {f"FRED:{spec[0]}": spec for spec in FRED_SERIES}
        now = utcnow()
        for series_id in missing:
            spec = spec_by_id.get(series_id)
            if spec is None:
                continue
            (_short, name, frequency, units, sa_flag, category, affected) = spec
            session.add(
                Series(
                    series_id=series_id,
                    name=name,
                    source="fred",
                    frequency=frequency,
                    units=units,
                    seasonal_adjustment=sa_flag,
                    category=category,
                    affected_instruments=list(affected),
                    series_metadata={},
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
