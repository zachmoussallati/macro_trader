"""Google Trends ingester via pytrends.

Pytrends rate-limits aggressively. We pull one query at a time with a
short trailing window. ``QUERY_TO_INSTRUMENTS`` maps each query term to the
instruments it informs (used by Stage 4+ signals).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.data.ingestion.base import Ingester
from macro_trader.data.lineage import IngestStats, LineageRecord
from macro_trader.db.models.alt_data import GoogleTrends
from macro_trader.utils.dates import ensure_aware, utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


QUERY_TO_INSTRUMENTS: dict[str, tuple[str, ...]] = {
    "recession": (),
    "inflation": ("GC", "SI"),
    "oil price": ("CL", "BZ"),
    "gas prices": ("RB",),
    "buy gold": ("GC",),
    "copper price": ("HG",),
}


@dataclass
class GoogleTrendsRaw:
    rows: list[dict[str, Any]] = field(default_factory=list)


class GoogleTrendsIngester(Ingester[GoogleTrendsRaw]):
    source_id = "google_trends"
    series_or_table = "alt_data.google_trends"
    expected_frequency = "daily"
    fetch_method = "api"

    DEFAULT_LOOKBACK_DAYS = 90
    REGION = "US"

    def __init__(self, *, session_factory, settings, queries: list[str] | None = None) -> None:
        super().__init__(session_factory=session_factory, settings=settings)
        self.queries = queries or list(QUERY_TO_INSTRUMENTS.keys())

    def fetch(self, *, since: datetime | None = None) -> GoogleTrendsRaw:
        from pytrends.request import TrendReq  # local import — heavy dep

        start = since or (utcnow() - timedelta(days=self.DEFAULT_LOOKBACK_DAYS))
        end = utcnow()
        tf = f"{start.date().isoformat()} {end.date().isoformat()}"

        raw = GoogleTrendsRaw()
        pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 30))
        for query in self.queries:
            try:
                pytrends.build_payload([query], cat=0, timeframe=tf, geo=self.REGION)
                df = pytrends.interest_over_time()
            except Exception as exc:
                self.log.warning("ingest.google_trends.query_failed", query=query, error=str(exc))
                time.sleep(5)
                continue
            if df is None or df.empty:
                continue
            for ts, row in df.iterrows():
                raw.rows.append(
                    {
                        "query": query,
                        "value_ts": ensure_aware(ts.to_pydatetime()),
                        "value": float(row[query]),
                    }
                )
            time.sleep(2)  # be polite

        return raw

    def transform(self, raw: GoogleTrendsRaw) -> list[dict[str, Any]]:
        now = utcnow()
        return [
            {
                "query_term": r["query"],
                "region": self.REGION,
                "value_ts": r["value_ts"],
                "observation_ts": now,
                "value": r["value"],
                "affected_instruments": list(QUERY_TO_INSTRUMENTS.get(r["query"], ())),
                "source": "google_trends",
            }
            for r in raw.rows
        ]

    def persist(
        self,
        session: Session,
        rows: list[dict[str, Any]],
        lineage: LineageRecord,
    ) -> IngestStats:
        stats = IngestStats()
        if not rows:
            return stats
        payload = [{**r, "lineage_id": lineage.lineage_id} for r in rows]
        stmt = pg_insert(GoogleTrends).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["query_term", "region", "value_ts", "observation_ts"],
            set_={
                "value": stmt.excluded.value,
                "affected_instruments": stmt.excluded.affected_instruments,
                "lineage_id": stmt.excluded.lineage_id,
            },
        )
        result = session.execute(stmt)
        stats.rows_ingested = result.rowcount or len(payload)
        return stats
