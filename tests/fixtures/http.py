"""Realistic provider-payload builders for ingester tests.

Each builder returns the shape that the corresponding upstream API
(``fredapi``, ``yfinance``, ``pytrends``) or HTTP endpoint (CFTC, EIA,
USDA, NOAA) actually returns. Comments cite where each shape was
verified.

Knobs every builder accepts:

- ``empty=True``    — return an empty-but-valid response
- ``malformed=True`` — return a structurally-broken response so the
  ingester's parse / transform path takes its error branch

Returned objects are immutable from the test's perspective — copy if
mutating in-place.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timedelta
from typing import Any

import pandas as pd


# ----------------------------------------------------------------------
# FRED (via fredapi.Fred.get_series_all_releases)
# ----------------------------------------------------------------------
# Shape verified against fredapi 0.5.2 source: the function returns a
# DataFrame indexed 0..n with columns ``date`` (pd.Timestamp), ``realtime_start``
# (pd.Timestamp), ``value`` (float or NaN).
def fred_releases_df(
    *,
    n_dates: int = 5,
    n_vintages: int = 2,
    empty: bool = False,
    malformed: bool = False,
) -> pd.DataFrame:
    if empty:
        return pd.DataFrame(columns=["date", "realtime_start", "value"])
    if malformed:
        # Missing required column 'value'.
        return pd.DataFrame(
            {
                "date": [pd.Timestamp("2025-01-01")],
                "realtime_start": [pd.Timestamp("2025-01-02")],
            }
        )

    base_date = pd.Timestamp("2025-01-01")
    rows = []
    for i in range(n_dates):
        date = base_date + pd.Timedelta(days=i)
        for v in range(n_vintages):
            rows.append(
                {
                    "date": date,
                    "realtime_start": date + pd.Timedelta(days=v),
                    "value": 100.0 + i + 0.1 * v,
                }
            )
    return pd.DataFrame(rows)


class FakeFred:
    """A drop-in replacement for ``fredapi.Fred``.

    Constructed with a dict mapping series_id -> DataFrame (or Exception
    instance, which is raised on call).
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[str] = []

    def get_series_all_releases(self, series_id: str) -> pd.DataFrame:
        self.calls.append(series_id)
        resp = self.responses.get(series_id)
        if isinstance(resp, BaseException):
            raise resp
        if resp is None:
            # Default: a small valid payload.
            return fred_releases_df(n_dates=2, n_vintages=1)
        return resp


# ----------------------------------------------------------------------
# yfinance (via yf.download)
# ----------------------------------------------------------------------
# Shape verified against yfinance 0.2.50: ``download(ticker, ...)`` returns
# a DataFrame with DatetimeIndex (named 'Date') and columns
# Open / High / Low / Close / Adj Close / Volume.
def yfinance_bars_df(
    *,
    n_bars: int = 5,
    empty: bool = False,
    malformed: bool = False,
    start: datetime | None = None,
) -> pd.DataFrame:
    if empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"])
    start = start or datetime(2025, 1, 2)
    if malformed:
        # All-NaN row — ingester's dropna(how='all') filters it.
        idx = pd.DatetimeIndex([start, start + timedelta(days=1)], name="Date")
        return pd.DataFrame(
            {
                "Open": [float("nan")] * 2,
                "High": [float("nan")] * 2,
                "Low": [float("nan")] * 2,
                "Close": [float("nan")] * 2,
                "Adj Close": [float("nan")] * 2,
                "Volume": [float("nan")] * 2,
            },
            index=idx,
        )

    dates = [start + timedelta(days=i) for i in range(n_bars)]
    idx = pd.DatetimeIndex(dates, name="Date")
    return pd.DataFrame(
        {
            "Open": [70.0 + i for i in range(n_bars)],
            "High": [72.0 + i for i in range(n_bars)],
            "Low": [69.0 + i for i in range(n_bars)],
            "Close": [71.0 + i for i in range(n_bars)],
            "Adj Close": [71.0 + i for i in range(n_bars)],
            "Volume": [1_000_000 + i * 1000 for i in range(n_bars)],
        },
        index=idx,
    )


class FakeYFinance:
    """Drop-in for the ``yfinance`` module's ``download`` entry point."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []

    def download(self, ticker: str, **kwargs: Any) -> pd.DataFrame:
        self.calls.append({"ticker": ticker, **kwargs})
        resp = self.responses.get(ticker)
        if isinstance(resp, BaseException):
            raise resp
        if resp is None:
            return yfinance_bars_df(n_bars=3, start=datetime(2025, 1, 6))
        return resp


# ----------------------------------------------------------------------
# CFTC (httpx GET, returns ZIP containing one .txt)
# ----------------------------------------------------------------------
# Disaggregated COT column headers verified against the actual file
# ``com_disagg_txt_2024.zip`` (~Sep 2024 snapshot).
_DISAGG_HEADERS = [
    "Market_and_Exchange_Names",
    "Report_Date_as_YYYY-MM-DD",
    "CFTC_Contract_Market_Code",
    "Open_Interest_All",
    "Prod_Merc_Positions_Long_All",
    "Prod_Merc_Positions_Short_All",
    "Swap_Positions_Long_All",
    "Swap__Positions_Short_All",
    "M_Money_Positions_Long_All",
    "M_Money_Positions_Short_All",
    "Other_Rept_Positions_Long_All",
    "Other_Rept_Positions_Short_All",
    "NonRept_Positions_Long_All",
    "NonRept_Positions_Short_All",
]


_LEGACY_HEADERS = [
    "Market_and_Exchange_Names",
    "Report_Date_as_YYYY-MM-DD",
    "CFTC_Contract_Market_Code",
    "Open_Interest_All",
    "Comm_Positions_Long_All",
    "Comm_Positions_Short_All",
    "NonComm_Positions_Long_All",
    "NonComm_Positions_Short_All",
    "NonRept_Positions_Long_All",
    "NonRept_Positions_Short_All",
]


_TFF_HEADERS = [
    "Market_and_Exchange_Names",
    "Report_Date_as_YYYY-MM-DD",
    "CFTC_Contract_Market_Code",
    "Open_Interest_All",
    "Asset_Mgr_Positions_Long_All",
    "Asset_Mgr_Positions_Short_All",
    "Lev_Money_Positions_Long_All",
    "Lev_Money_Positions_Short_All",
    "NonRept_Positions_Long_All",
    "NonRept_Positions_Short_All",
]


def cftc_zip_bytes(
    *,
    report_type: str = "disaggregated",
    empty: bool = False,
    malformed: bool = False,
    instrument_rows: dict[str, str] | None = None,
) -> bytes:
    """Build a CFTC weekly report ZIP.

    ``instrument_rows``: optional mapping ``cftc_code -> date_str`` to fix the
    rows produced. Defaults to a CL + GC pair for the latest Tuesday.
    """
    headers = {
        "disaggregated": _DISAGG_HEADERS,
        "legacy": _LEGACY_HEADERS,
        "financial_tff": _TFF_HEADERS,
    }[report_type]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        text_buf = io.StringIO()
        if not malformed:
            writer = csv.writer(text_buf)
            writer.writerow(headers)
            if not empty:
                rows_to_write = instrument_rows or {
                    "067651": "2025-01-07",  # CL
                    "088691": "2025-01-07",  # GC
                }
                for code, date_str in rows_to_write.items():
                    row = ["MARKET", date_str, code] + ["1000"] * (len(headers) - 3)
                    writer.writerow(row)
        else:
            # Malformed: write a row missing the date column.
            writer = csv.writer(text_buf)
            writer.writerow(headers)
            writer.writerow(["MARKET", "", "067651"] + ["1000"] * (len(headers) - 3))
        # The inner file's stem matches the URL stem CFTC uses; .txt suffix.
        zf.writestr(f"{report_type}.txt", text_buf.getvalue())
    return buf.getvalue()


def cftc_zip_no_txt_bytes() -> bytes:
    """A ZIP containing no .txt files — exercises the empty-namelist branch."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("readme.md", "no data here")
    return buf.getvalue()


# ----------------------------------------------------------------------
# EIA v2 (httpx GET on api.eia.gov/v2/seriesid/{id})
# ----------------------------------------------------------------------
# Shape verified against the v2 API spec: the response body is
# ``{"response": {"data": [...]}, ...}`` where each datum has ``period``
# (YYYY-MM-DD) and ``value`` (number or null).
def eia_series_response(
    *,
    n_rows: int = 3,
    empty: bool = False,
    malformed: bool = False,
) -> dict[str, Any]:
    if malformed:
        return {"unexpected": "shape"}
    if empty:
        return {"response": {"data": []}}
    data = [
        {
            "period": (datetime(2025, 1, 1) + timedelta(days=i * 7)).strftime("%Y-%m-%d"),
            "value": 100.0 + i,
        }
        for i in range(n_rows)
    ]
    return {"response": {"data": data}}


# ----------------------------------------------------------------------
# USDA NASS Quick Stats
# ----------------------------------------------------------------------
# Shape verified against the public API: body is ``{"data": [...]}`` where
# each entry has ``year``, ``Value`` (string with commas), ``unit_desc``,
# and many other commodity-specific fields we ignore.
def usda_quickstats_response(
    *,
    n_rows: int = 3,
    empty: bool = False,
    malformed: bool = False,
) -> dict[str, Any]:
    if malformed:
        return {"data": "not a list"}
    if empty:
        return {"data": []}
    data = [
        {
            "year": str(2022 + i),
            "Value": f"{15_000 + i:,}",
            "unit_desc": "BU",
        }
        for i in range(n_rows)
    ]
    return {"data": data}


# ----------------------------------------------------------------------
# NOAA CDO (Climate Data Online)
# ----------------------------------------------------------------------
# Shape: ``{"results": [{"date": "2025-01-01T00:00:00", "value": ...}, ...]}``.
def noaa_gsom_response(
    *,
    n_rows: int = 3,
    empty: bool = False,
    malformed: bool = False,
) -> dict[str, Any]:
    if malformed:
        # Date in unparseable format — ingester's transform skips it.
        return {"results": [{"date": "not-a-date", "value": 10}]}
    if empty:
        return {}
    results = [
        {
            "date": (datetime(2025, 1, 1) + timedelta(days=i * 30)).isoformat(),
            "value": 50 + i,
        }
        for i in range(n_rows)
    ]
    return {"results": results}


# ----------------------------------------------------------------------
# Google Trends (via pytrends.request.TrendReq)
# ----------------------------------------------------------------------
def pytrends_interest_df(
    *,
    query: str = "oil price",
    n_rows: int = 5,
    empty: bool = False,
    malformed: bool = False,
) -> pd.DataFrame | None:
    if empty:
        return pd.DataFrame()
    if malformed:
        # Returns df with no column named after the query — transform
        # would raise KeyError; the fetch loop's try/except catches it.
        return pd.DataFrame({"other": [1, 2, 3]})
    idx = pd.DatetimeIndex(
        [datetime(2025, 1, 1) + timedelta(days=i * 7) for i in range(n_rows)]
    )
    return pd.DataFrame({query: [50 + i for i in range(n_rows)]}, index=idx)


class FakePytrends:
    """Drop-in for ``pytrends.request.TrendReq``.

    Pytrends' real flow is ``build_payload(...)`` then ``interest_over_time()``.
    The fake records both calls and returns whatever the constructor was given
    for the most-recently-built query.
    """

    def __init__(
        self,
        *,
        responses: dict[str, Any] | None = None,
        build_payload_error: BaseException | None = None,
        **_init_kwargs: Any,
    ) -> None:
        self.responses = responses or {}
        self.build_payload_error = build_payload_error
        self.build_calls: list[list[str]] = []
        self.interest_calls: int = 0
        self._last_query: str | None = None

    def build_payload(
        self,
        kw_list: list[str],
        *,
        cat: int = 0,
        timeframe: str = "today 12-m",
        geo: str = "",
    ) -> None:
        self.build_calls.append(list(kw_list))
        if self.build_payload_error is not None:
            raise self.build_payload_error
        self._last_query = kw_list[0]

    def interest_over_time(self) -> pd.DataFrame | None:
        self.interest_calls += 1
        query = self._last_query or ""
        resp = self.responses.get(query)
        if isinstance(resp, BaseException):
            raise resp
        if resp is None:
            return pytrends_interest_df(query=query, n_rows=3)
        return resp


# ----------------------------------------------------------------------
# Generic helpers
# ----------------------------------------------------------------------
def to_json_bytes(payload: dict[str, Any] | list[Any]) -> bytes:
    """Encode payload as UTF-8 JSON bytes (for raw httpx mocks)."""
    return json.dumps(payload).encode("utf-8")
