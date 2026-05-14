"""CFTC Commitments of Traders ingester.

Downloads the weekly disaggregated futures-only report and parses it into
``positioning.cot_weekly`` rows. The disaggregated report covers all the
commodities in our universe; legacy/TFF can be wired similarly later.

The disaggregated text file URL (refresh weekly): the historical CSV is
at https://www.cftc.gov/files/dea/history/com_disagg_txt_*.zip; for current
year we hit the JSON ``deacotreports`` endpoint via the configured base.

Stage 2 scope: only the current-year disaggregated report. Historical
backfill is a separate operation documented in usage.md.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import retry, stop_after_attempt, wait_exponential

from macro_trader.data.ingestion.base import Ingester
from macro_trader.data.lineage import IngestStats, LineageRecord
from macro_trader.db.models.positioning import COTWeekly
from macro_trader.utils.dates import ensure_aware

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# CFTC contract code → our instrument_id. Codes are the first 6 digits of
# the CFTC commodity classification number.
CFTC_CODE_TO_INSTRUMENT: dict[str, str] = {
    "067651": "CL",  # WTI Crude Oil
    "067411": "BZ",  # Brent Crude (latest)  — code varies; verify quarterly
    "023391": "NG",  # Henry Hub Natural Gas
    "022651": "HO",  # Heating Oil
    "111659": "RB",  # RBOB Gasoline
    "085692": "HG",  # Copper
    "088691": "GC",  # Gold
    "084691": "SI",  # Silver
    "076651": "PL",  # Platinum
    "002602": "ZC",  # Corn
    "005602": "ZS",  # Soybeans
    "001602": "ZW",  # Wheat (CBOT)
}


@dataclass
class CFTCRaw:
    rows: list[dict[str, Any]] = field(default_factory=list)


class CFTCIngester(Ingester[CFTCRaw]):
    source_id = "cftc"
    series_or_table = "positioning.cot_weekly"
    expected_frequency = "weekly"
    fetch_method = "csv_download"

    DISAGG_HISTORICAL_URL = "https://www.cftc.gov/files/dea/history/com_disagg_txt_{year}.zip"

    def __init__(self, *, session_factory, settings) -> None:
        super().__init__(session_factory=session_factory, settings=settings)

    def fetch(self, *, since: datetime | None = None) -> CFTCRaw:
        year = (since or datetime.utcnow()).year
        url = self.DISAGG_HISTORICAL_URL.format(year=year)
        content = self._download(url)
        rows = self._parse_zip(content)
        return CFTCRaw(rows=rows)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30), reraise=True)
    def _download(self, url: str) -> bytes:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content

    def _parse_zip(self, content: bytes) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            inner_names = [n for n in zf.namelist() if n.endswith(".txt")]
            if not inner_names:
                return rows
            with zf.open(inner_names[0]) as fh:
                text = io.TextIOWrapper(fh, encoding="latin-1")
                reader = csv.DictReader(text)
                for row in reader:
                    cftc_code = (row.get("CFTC_Contract_Market_Code") or "").strip()
                    instrument_id = CFTC_CODE_TO_INSTRUMENT.get(cftc_code)
                    if instrument_id is None:
                        continue
                    rows.append({"_raw": row, "instrument_id": instrument_id})
        return rows

    def transform(self, raw: CFTCRaw) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for entry in raw.rows:
            r = entry["_raw"]
            instrument_id = entry["instrument_id"]
            try:
                report_dt = datetime.strptime(
                    (r.get("Report_Date_as_YYYY-MM-DD") or "").strip(),
                    "%Y-%m-%d",
                )
            except (ValueError, KeyError):
                continue
            out.append(
                {
                    "report_ts": ensure_aware(report_dt),
                    "instrument_id": instrument_id,
                    "report_type": "disaggregated",
                    "publication_ts": ensure_aware(report_dt),
                    "cftc_contract_code": (r.get("CFTC_Contract_Market_Code") or "").strip(),
                    "open_interest": _opt_float(r.get("Open_Interest_All")),
                    "producer_long": _opt_float(r.get("Prod_Merc_Positions_Long_All")),
                    "producer_short": _opt_float(r.get("Prod_Merc_Positions_Short_All")),
                    "swap_long": _opt_float(r.get("Swap_Positions_Long_All")),
                    "swap_short": _opt_float(r.get("Swap__Positions_Short_All")),
                    "managed_money_long": _opt_float(r.get("M_Money_Positions_Long_All")),
                    "managed_money_short": _opt_float(r.get("M_Money_Positions_Short_All")),
                    "other_reportable_long": _opt_float(r.get("Other_Rept_Positions_Long_All")),
                    "other_reportable_short": _opt_float(r.get("Other_Rept_Positions_Short_All")),
                    "nonreportable_long": _opt_float(r.get("NonRept_Positions_Long_All")),
                    "nonreportable_short": _opt_float(r.get("NonRept_Positions_Short_All")),
                    "source": "cftc",
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
        stmt = pg_insert(COTWeekly).values(payload)
        cols = {
            c: getattr(stmt.excluded, c)
            for c in (
                "publication_ts",
                "cftc_contract_code",
                "open_interest",
                "producer_long",
                "producer_short",
                "swap_long",
                "swap_short",
                "managed_money_long",
                "managed_money_short",
                "other_reportable_long",
                "other_reportable_short",
                "nonreportable_long",
                "nonreportable_short",
                "lineage_id",
            )
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["report_ts", "instrument_id", "report_type"],
            set_=cols,
        )
        result = session.execute(stmt)
        stats.rows_ingested = result.rowcount or len(payload)
        return stats


def _opt_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
