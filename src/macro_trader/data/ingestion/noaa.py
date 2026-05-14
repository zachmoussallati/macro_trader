"""NOAA Climate Data Online (CDO) ingester.

Pulls daily HDD / CDD aggregates from the GSOM (Global Summary of the
Month) dataset for a small set of population-weighted regions. Stage 2
keeps the scope minimal — one US-aggregate "station" pulling the
``CLDD`` and ``HTDD`` data types — and we expand region/granularity as
weather-driven signals come online.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import retry, stop_after_attempt, wait_exponential

from macro_trader.data.ingestion.base import Ingester
from macro_trader.data.lineage import IngestStats, LineageRecord
from macro_trader.db.models.alt_data import WeatherData
from macro_trader.utils.dates import ensure_aware, utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# (cdo_station_id, our_station_id, region, affected_instruments)
# FIPS aggregates are population-weighted at the area level — sufficient
# for HDD/CDD demand signals on natural gas + electricity (NG, HO) and
# growing-season weather signals on grains (ZC, ZS, ZW). Brazilian Mato
# Grosso (soy) and Pampas (Argentina) coverage requires non-NOAA sources;
# documented as deferred in docs/data_sources.md.
STATIONS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("FIPS:US", "US_AGG", "US", ("NG", "HO")),
    # Corn Belt centre — drives corn + soy yield expectations.
    ("FIPS:19", "IA_CORN_BELT", "US-IA", ("ZC", "ZS")),
    # Illinois — second-largest corn / soy producer.
    ("FIPS:17", "IL_CORN_BELT", "US-IL", ("ZC", "ZS")),
    # Kansas — winter wheat heartland.
    ("FIPS:20", "KS_WHEAT_BELT", "US-KS", ("ZW",)),
)


# (datatype_id, our_metric)
DATATYPES: tuple[tuple[str, str], ...] = (
    ("HTDD", "hdd"),
    ("CLDD", "cdd"),
)


@dataclass
class NOAARaw:
    rows: list[dict[str, Any]] = field(default_factory=list)


class NOAAIngester(Ingester[NOAARaw]):
    source_id = "noaa"
    series_or_table = "alt_data.weather_data"
    expected_frequency = "daily"
    fetch_method = "api"
    BASE = "https://www.ncei.noaa.gov/cdo-web/api/v2/data"

    DEFAULT_LOOKBACK_DAYS = 90

    def __init__(self, *, session_factory, settings, api_key: str | None = None) -> None:
        super().__init__(session_factory=session_factory, settings=settings)
        self.api_key = api_key or settings.data_sources.noaa_api_key
        if not self.api_key:
            raise ValueError("NOAA_API_KEY missing")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30), reraise=True)
    def _fetch_chunk(
        self,
        client: httpx.Client,
        *,
        station_id: str,
        datatype_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        params = {
            "datasetid": "GSOM",
            "locationid": station_id,
            "datatypeid": datatype_id,
            "startdate": start.date().isoformat(),
            "enddate": end.date().isoformat(),
            "limit": 1000,
            "units": "standard",
        }
        response = client.get(
            self.BASE,
            params=params,
            headers={"token": self.api_key},
        )
        response.raise_for_status()
        body = response.json()
        return body.get("results") or []

    def fetch(self, *, since: datetime | None = None) -> NOAARaw:
        start = since or (utcnow() - timedelta(days=self.DEFAULT_LOOKBACK_DAYS))
        end = utcnow()
        raw = NOAARaw()
        with httpx.Client(timeout=60.0) as client:
            for cdo_station, our_station, region, affected in STATIONS:
                for datatype_id, metric in DATATYPES:
                    try:
                        chunk = self._fetch_chunk(
                            client,
                            station_id=cdo_station,
                            datatype_id=datatype_id,
                            start=start,
                            end=end,
                        )
                    except Exception as exc:
                        self.log.warning(
                            "ingest.noaa.chunk_failed",
                            station=cdo_station,
                            datatype=datatype_id,
                            error=str(exc),
                        )
                        continue
                    for row in chunk:
                        raw.rows.append(
                            {
                                "_row": row,
                                "station_id": our_station,
                                "metric": metric,
                                "region": region,
                                "affected": affected,
                            }
                        )
        return raw

    def transform(self, raw: NOAARaw) -> list[dict[str, Any]]:
        now = utcnow()
        out: list[dict[str, Any]] = []
        for entry in raw.rows:
            r = entry["_row"]
            try:
                value_ts = ensure_aware(datetime.fromisoformat(r["date"].replace("Z", "+00:00")))
            except (KeyError, ValueError):
                continue
            value = r.get("value")
            try:
                value = float(value) if value is not None else None
            except (TypeError, ValueError):
                value = None
            out.append(
                {
                    "station_id": entry["station_id"],
                    "value_ts": value_ts,
                    "metric": entry["metric"],
                    "observation_ts": now,
                    "value": value,
                    "region": entry["region"],
                    "affected_instruments": list(entry["affected"]),
                    "source": "noaa",
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
        stmt = pg_insert(WeatherData).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["station_id", "value_ts", "metric", "observation_ts"],
            set_={
                "value": stmt.excluded.value,
                "region": stmt.excluded.region,
                "affected_instruments": stmt.excluded.affected_instruments,
                "lineage_id": stmt.excluded.lineage_id,
            },
        )
        result = session.execute(stmt)
        stats.rows_ingested = result.rowcount or len(payload)
        return stats
