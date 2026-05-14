"""USDA NASS Quick Stats ingester (WASDE / Crop Progress / Grain Stocks).

The QuickStats endpoint returns the underlying survey data series. Stage 2
pulls a compact, well-defined slice: production / yield / ending stocks for
corn, soybeans, and wheat at the US level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from macro_trader.data.ingestion.base import Ingester
from macro_trader.data.lineage import IngestStats, LineageRecord
from macro_trader.db.models.alt_data import USDAReport
from macro_trader.utils.dates import ensure_aware, utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Each query targets one (commodity, statistic) pair. Stage 2 covers
# production / yield / ending stocks for the big three grains. Extend in
# later stages.
QUERIES: tuple[tuple[str, str, str, str, dict[str, str]], ...] = (
    # (report_type, commodity, instrument_id, metric, params)
    (
        "wasde",
        "corn",
        "ZC",
        "production",
        {
            "commodity_desc": "CORN",
            "statisticcat_desc": "PRODUCTION",
            "agg_level_desc": "NATIONAL",
            "freq_desc": "ANNUAL",
        },
    ),
    (
        "wasde",
        "corn",
        "ZC",
        "yield",
        {
            "commodity_desc": "CORN",
            "statisticcat_desc": "YIELD",
            "agg_level_desc": "NATIONAL",
            "freq_desc": "ANNUAL",
        },
    ),
    (
        "wasde",
        "soybeans",
        "ZS",
        "production",
        {
            "commodity_desc": "SOYBEANS",
            "statisticcat_desc": "PRODUCTION",
            "agg_level_desc": "NATIONAL",
            "freq_desc": "ANNUAL",
        },
    ),
    (
        "wasde",
        "soybeans",
        "ZS",
        "yield",
        {
            "commodity_desc": "SOYBEANS",
            "statisticcat_desc": "YIELD",
            "agg_level_desc": "NATIONAL",
            "freq_desc": "ANNUAL",
        },
    ),
    (
        "wasde",
        "wheat",
        "ZW",
        "production",
        {
            "commodity_desc": "WHEAT",
            "statisticcat_desc": "PRODUCTION",
            "agg_level_desc": "NATIONAL",
            "freq_desc": "ANNUAL",
        },
    ),
    (
        "wasde",
        "wheat",
        "ZW",
        "yield",
        {
            "commodity_desc": "WHEAT",
            "statisticcat_desc": "YIELD",
            "agg_level_desc": "NATIONAL",
            "freq_desc": "ANNUAL",
        },
    ),
)


@dataclass
class USDARaw:
    chunks: list[tuple[str, str, str, str, list[dict[str, Any]]]] = field(default_factory=list)


class USDAIngester(Ingester[USDARaw]):
    source_id = "usda"
    series_or_table = "alt_data.usda_reports"
    expected_frequency = "monthly"
    fetch_method = "api"
    BASE = "https://quickstats.nass.usda.gov/api/api_GET"

    def __init__(self, *, session_factory, settings, api_key: str | None = None) -> None:
        super().__init__(session_factory=session_factory, settings=settings)
        self.api_key = api_key or settings.data_sources.usda_api_key
        if not self.api_key:
            raise ValueError("USDA_API_KEY missing")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30), reraise=True)
    def _fetch_one(self, client: httpx.Client, params: dict[str, str]) -> list[dict[str, Any]]:
        merged = {"key": self.api_key, "format": "JSON", **params}
        response = client.get(self.BASE, params=merged)
        response.raise_for_status()
        body = response.json()
        return body.get("data") or []

    def fetch(self, *, since: datetime | None = None) -> USDARaw:
        raw = USDARaw()
        with httpx.Client(timeout=60.0) as client:
            for report_type, commodity, instrument_id, metric, params in QUERIES:
                try:
                    rows = self._fetch_one(client, params)
                except Exception as exc:
                    self.log.warning(
                        "ingest.usda.query_failed",
                        commodity=commodity,
                        metric=metric,
                        error=str(exc),
                    )
                    continue
                raw.chunks.append((report_type, commodity, instrument_id, metric, rows))
        return raw

    def transform(self, raw: USDARaw) -> list[dict[str, Any]]:
        now = utcnow()
        out: list[dict[str, Any]] = []
        for report_type, commodity, instrument_id, metric, rows in raw.chunks:
            for r in rows:
                year_str = r.get("year")
                value_str = r.get("Value")
                if not year_str:
                    continue
                try:
                    value_ts = ensure_aware(datetime(int(year_str), 12, 31))
                except (TypeError, ValueError):
                    continue
                try:
                    value = (
                        float(str(value_str).replace(",", ""))
                        if value_str not in (None, "(D)")
                        else None
                    )
                except (TypeError, ValueError):
                    value = None
                out.append(
                    {
                        "report_type": report_type,
                        "value_ts": value_ts,
                        "observation_ts": now,
                        "commodity": commodity,
                        "metric": metric,
                        "value": value,
                        "units": r.get("unit_desc"),
                        "affected_instruments": [instrument_id],
                        "source": "usda",
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
        # USDA reports have a synthetic report_id PK; there's no natural-key
        # uniqueness across (report_type, commodity, metric, value_ts), so we
        # delete-and-insert per (report_type, commodity, metric, value_ts) to
        # stay idempotent.

        for r in rows:
            session.query(USDAReport).filter_by(
                report_type=r["report_type"],
                value_ts=r["value_ts"],
                commodity=r["commodity"],
                metric=r["metric"],
            ).delete(synchronize_session=False)
        payload = [{**r, "lineage_id": lineage.lineage_id} for r in rows]
        session.bulk_insert_mappings(USDAReport, payload)
        stats.rows_ingested = len(payload)
        return stats
