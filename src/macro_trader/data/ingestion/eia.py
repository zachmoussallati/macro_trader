"""EIA Open Data ingester (weekly petroleum + nat gas storage)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import retry, stop_after_attempt, wait_exponential

from macro_trader.data.ingestion.base import Ingester
from macro_trader.data.lineage import IngestStats, LineageRecord
from macro_trader.db.models.alt_data import EIAInventory
from macro_trader.utils.dates import ensure_aware, utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# (eia_series_id, units, affected_instruments)
EIA_SERIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("PET.WCRSTUS1.W", "Thousand Barrels", ("CL", "BZ")),
    ("PET.WGTSTUS1.W", "Thousand Barrels", ("RB",)),
    ("PET.WDISTUS1.W", "Thousand Barrels", ("HO",)),
    ("PET.WCRRRUS2.W", "Thousand Barrels", ("CL",)),
    ("NG.NW2_EPG0_SWO_R48_BCF.W", "Bcf", ("NG",)),
)


@dataclass
class EIARaw:
    series: list[tuple[str, list[dict[str, Any]], tuple[str, ...]]] = field(default_factory=list)


class EIAIngester(Ingester[EIARaw]):
    source_id = "eia"
    series_or_table = "alt_data.eia_inventory"
    expected_frequency = "weekly"
    fetch_method = "api"
    BASE = "https://api.eia.gov/v2/seriesid"

    def __init__(self, *, session_factory, settings, api_key: str | None = None) -> None:
        super().__init__(session_factory=session_factory, settings=settings)
        self.api_key = api_key or settings.data_sources.eia_api_key
        if not self.api_key:
            raise ValueError("EIA_API_KEY missing")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30), reraise=True)
    def _fetch_one(self, client: httpx.Client, series_id: str) -> list[dict[str, Any]]:
        url = f"{self.BASE}/{series_id}"
        response = client.get(url, params={"api_key": self.api_key})
        response.raise_for_status()
        body = response.json()
        return body.get("response", {}).get("data") or []

    def fetch(self, *, since: datetime | None = None) -> EIARaw:
        raw = EIARaw()
        with httpx.Client(timeout=30.0) as client:
            for series_id, _units, affected in EIA_SERIES:
                try:
                    data = self._fetch_one(client, series_id)
                except Exception as exc:
                    self.log.warning("ingest.eia.series_failed", series=series_id, error=str(exc))
                    continue
                raw.series.append((series_id, data, affected))
        return raw

    def transform(self, raw: EIARaw) -> list[dict[str, Any]]:
        now = utcnow()
        out: list[dict[str, Any]] = []
        for series_id, data, affected in raw.series:
            units = next((u for sid, u, _ in EIA_SERIES if sid == series_id), None)
            for row in data:
                period = row.get("period")
                value = row.get("value")
                if period is None:
                    continue
                try:
                    value_ts = ensure_aware(datetime.strptime(period, "%Y-%m-%d"))
                except (TypeError, ValueError):
                    continue
                try:
                    value = float(value) if value is not None else None
                except (TypeError, ValueError):
                    value = None
                out.append(
                    {
                        "series_id": series_id,
                        "value_ts": value_ts,
                        "observation_ts": now,
                        "value": value,
                        "units": units,
                        "affected_instruments": list(affected),
                        "source": "eia",
                    }
                )
        return out

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
        stmt = pg_insert(EIAInventory).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["series_id", "value_ts", "observation_ts"],
            set_={
                "value": stmt.excluded.value,
                "units": stmt.excluded.units,
                "affected_instruments": stmt.excluded.affected_instruments,
                "lineage_id": stmt.excluded.lineage_id,
            },
        )
        result = session.execute(stmt)
        stats.rows_ingested = result.rowcount or len(payload)
        return stats
