"""CFTC Commitments of Traders ingester.

Stage 3 covers three report types:

- ``disaggregated`` (commodity-focused; was the only Stage 2 wiring)
- ``legacy`` (older commercial vs non-commercial split — needed for
  Stage 4 positioning signals)
- ``financial_tff`` (Traders in Financial Futures; included for completeness
  on FX/rates instruments arriving in later stages)

The downloads are weekly ZIPs hosted on cftc.gov, partitioned by year. The
Stage 2 implementation pulled only the current year, which silently
returned zero rows during the Jan 1-7 window before the new file gets
populated. Stage 3 fix: between Jan 1 and Jan 14, fetch BOTH the prior
year and current year ZIPs and merge; dedupe by natural key inside
``transform``.
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
from macro_trader.utils.dates import ensure_aware, utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# CFTC contract code → our instrument_id. Codes are the first 6 digits of
# the CFTC commodity classification number. Verify quarterly: Brent and
# refined-product codes have rotated in past CFTC releases.
CFTC_CODE_TO_INSTRUMENT: dict[str, str] = {
    "067651": "CL",
    "067411": "BZ",
    "023391": "NG",
    "022651": "HO",
    "111659": "RB",
    "085692": "HG",
    "088691": "GC",
    "084691": "SI",
    "076651": "PL",
    "002602": "ZC",
    "005602": "ZS",
    "001602": "ZW",
}


# (report_type, base_url_template). ``{year}`` is the calendar year.
_REPORTS: tuple[tuple[str, str], ...] = (
    (
        "disaggregated",
        "https://www.cftc.gov/files/dea/history/com_disagg_txt_{year}.zip",
    ),
    (
        "legacy",
        "https://www.cftc.gov/files/dea/history/deacot{year}.zip",
    ),
    (
        "financial_tff",
        "https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip",
    ),
)


# Per-report column-name maps. The disaggregated report carries every field
# we model; legacy and TFF fill a subset and leave the rest NULL.
_DISAGG_COLS = {
    "open_interest": "Open_Interest_All",
    "producer_long": "Prod_Merc_Positions_Long_All",
    "producer_short": "Prod_Merc_Positions_Short_All",
    "swap_long": "Swap_Positions_Long_All",
    "swap_short": "Swap__Positions_Short_All",
    "managed_money_long": "M_Money_Positions_Long_All",
    "managed_money_short": "M_Money_Positions_Short_All",
    "other_reportable_long": "Other_Rept_Positions_Long_All",
    "other_reportable_short": "Other_Rept_Positions_Short_All",
    "nonreportable_long": "NonRept_Positions_Long_All",
    "nonreportable_short": "NonRept_Positions_Short_All",
}

# Legacy COT (commercial vs non-commercial breakdown).
# Producer-like rows go in producer_*; managed money / large speculators in
# nonreportable_* per the legacy taxonomy.
_LEGACY_COLS = {
    "open_interest": "Open_Interest_All",
    "producer_long": "Comm_Positions_Long_All",
    "producer_short": "Comm_Positions_Short_All",
    "managed_money_long": "NonComm_Positions_Long_All",
    "managed_money_short": "NonComm_Positions_Short_All",
    "nonreportable_long": "NonRept_Positions_Long_All",
    "nonreportable_short": "NonRept_Positions_Short_All",
}

# TFF (Traders in Financial Futures). Largely empty on commodity codes but
# the schema accepts it.
_TFF_COLS = {
    "open_interest": "Open_Interest_All",
    "managed_money_long": "Asset_Mgr_Positions_Long_All",
    "managed_money_short": "Asset_Mgr_Positions_Short_All",
    "swap_long": "Lev_Money_Positions_Long_All",
    "swap_short": "Lev_Money_Positions_Short_All",
    "nonreportable_long": "NonRept_Positions_Long_All",
    "nonreportable_short": "NonRept_Positions_Short_All",
}

_REPORT_COL_MAP: dict[str, dict[str, str]] = {
    "disaggregated": _DISAGG_COLS,
    "legacy": _LEGACY_COLS,
    "financial_tff": _TFF_COLS,
}


@dataclass
class CFTCRaw:
    rows: list[dict[str, Any]] = field(default_factory=list)


class CFTCIngester(Ingester[CFTCRaw]):
    source_id = "cftc"
    series_or_table = "positioning.cot_weekly"
    expected_frequency = "weekly"
    fetch_method = "csv_download"

    # Within this many days of the new year, also pull the prior year's ZIP
    # so we don't silently return zero rows before CFTC publishes the new
    # year's file.
    YEAR_BOUNDARY_BACKFILL_DAYS = 14

    def __init__(self, *, session_factory, settings) -> None:
        super().__init__(session_factory=session_factory, settings=settings)

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    def fetch(self, *, since: datetime | None = None) -> CFTCRaw:
        now = since or utcnow()
        years = self._years_to_fetch(now)
        raw = CFTCRaw()
        for year in years:
            for report_type, template in _REPORTS:
                url = template.format(year=year)
                try:
                    content = self._download(url)
                except Exception as exc:
                    self.log.warning(
                        "ingest.cftc.download_failed",
                        report_type=report_type,
                        year=year,
                        error=str(exc),
                    )
                    continue
                for parsed in self._parse_zip(content, report_type=report_type):
                    raw.rows.append(parsed)
        return raw

    def _years_to_fetch(self, now: datetime) -> list[int]:
        years = [now.year]
        day_of_year = now.timetuple().tm_yday
        if day_of_year <= self.YEAR_BOUNDARY_BACKFILL_DAYS:
            years.append(now.year - 1)
        return years

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30), reraise=True)
    def _download(self, url: str) -> bytes:
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content

    def _parse_zip(self, content: bytes, *, report_type: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            inner_names = [n for n in zf.namelist() if n.endswith(".txt")]
            if not inner_names:
                return out
            with zf.open(inner_names[0]) as fh:
                text = io.TextIOWrapper(fh, encoding="latin-1")
                reader = csv.DictReader(text)
                for row in reader:
                    cftc_code = (row.get("CFTC_Contract_Market_Code") or "").strip()
                    instrument_id = CFTC_CODE_TO_INSTRUMENT.get(cftc_code)
                    if instrument_id is None:
                        continue
                    out.append(
                        {
                            "_raw": row,
                            "instrument_id": instrument_id,
                            "report_type": report_type,
                        }
                    )
        return out

    # ------------------------------------------------------------------
    # Transform
    # ------------------------------------------------------------------
    def transform(self, raw: CFTCRaw) -> list[dict[str, Any]]:
        # Dedupe within the batch on natural key (report_ts, instrument_id,
        # report_type). The Jan year-boundary fix can produce overlap if the
        # provider has already pushed early-year rows into both files.
        seen: set[tuple[datetime, str, str]] = set()
        out: list[dict[str, Any]] = []
        for entry in raw.rows:
            r = entry["_raw"]
            instrument_id = entry["instrument_id"]
            report_type = entry["report_type"]
            try:
                report_dt = datetime.strptime(
                    (r.get("Report_Date_as_YYYY-MM-DD") or "").strip(),
                    "%Y-%m-%d",
                )
            except (ValueError, KeyError):
                continue
            report_ts = ensure_aware(report_dt)
            dedup_key = (report_ts, instrument_id, report_type)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            col_map = _REPORT_COL_MAP[report_type]
            row_out: dict[str, Any] = {
                "report_ts": report_ts,
                "instrument_id": instrument_id,
                "report_type": report_type,
                "publication_ts": report_ts,
                "cftc_contract_code": (r.get("CFTC_Contract_Market_Code") or "").strip(),
                "source": "cftc",
            }
            for canonical, source_col in col_map.items():
                row_out[canonical] = _opt_float(r.get(source_col))
            out.append(row_out)
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
