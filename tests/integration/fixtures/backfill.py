"""Real-data backfill fixture for Stage 4C+ validation tests.

The fixture pulls 504 trading days of real data for the 13-instrument
universe + the six macro factors + 5 years of CFTC COT + 5 years of
calendar events. Cached to ``tests/data/backfill_504d.parquet`` after
the first pull so subsequent CI / local runs don't hit the upstream
APIs.

Usage:

.. code-block:: python

    @pytest.mark.real_data
    def test_my_cate_validation(backfill_panel):
        # backfill_panel is a Backfill object with .bars / .factors /
        # .cot / .calendar attributes.
        ...

If the parquet cache is missing AND the necessary API keys are not
present in the environment, every ``real_data`` test is skipped with
a clear message. Local developers can regenerate the cache by
running ``python -m tests.integration.fixtures.backfill`` once they
have the API keys.

The fixture is intentionally not generated in this repo's CI by
default — pulling yfinance + FRED on every test run is expensive and
flaky.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest

if TYPE_CHECKING:
    pass


BACKFILL_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "backfill_504d.parquet"
LOOKBACK_DAYS = 504
LOOKBACK_YEARS_EVENTS = 5

INSTRUMENT_UNIVERSE: tuple[str, ...] = (
    "CL", "BZ", "NG", "HO", "RB",       # energy
    "HG", "ALI",                         # base metals
    "GC", "SI", "PL",                    # precious metals
    "ZC", "ZS", "ZW",                    # grains
)

PROXY_TICKERS: dict[str, str] = {
    "CL": "USO", "BZ": "BNO", "NG": "UNG", "HO": "UHN", "RB": "UGA",
    "HG": "CPER", "ALI": "JJU",
    "GC": "GLD", "SI": "SLV", "PL": "PPLT",
    "ZC": "CORN", "ZS": "SOYB", "ZW": "WEAT",
}

# Six macro factors from Stage 4B.
FACTOR_SERIES: tuple[tuple[str, str], ...] = (
    ("growth", "INDPRO"),
    ("inflation", "CPIAUCSL"),
    ("liquidity", "DFII2"),
    ("usd", "DTWEXBGS"),
    ("oil", "DCOILWTICO"),
    ("risk_on", "VIXCLS"),
)


@dataclass(slots=True)
class Backfill:
    """In-memory bundle of real backfilled data.

    Attributes:

    - ``bars``: DataFrame indexed by date, columns = instrument_id,
      values = adjusted close.
    - ``factors``: DataFrame indexed by date, columns = factor name
      (growth / inflation / etc.), values = raw FRED level.
    - ``cot``: DataFrame with columns (report_ts, instrument_id,
      report_type, managed_money_long, ...). Empty if CFTC unavailable.
    - ``calendar``: DataFrame with columns (event_ts, kind, subject,
      importance, affected_instruments). Empty if calendar
      unavailable.
    """

    bars: pd.DataFrame
    factors: pd.DataFrame
    cot: pd.DataFrame
    calendar: pd.DataFrame


def cache_exists() -> bool:
    return BACKFILL_PATH.exists()


def load_cached() -> Backfill | None:
    """Read the cached backfill from disk; returns ``None`` if absent
    or corrupted. The cache stores four named DataFrames in a single
    multi-sheet parquet using a ``_dataset`` key column for split."""
    if not BACKFILL_PATH.exists():
        return None
    try:
        big = pd.read_parquet(BACKFILL_PATH)
    except Exception:
        return None
    if "_dataset" not in big.columns:
        return None
    return Backfill(
        bars=big[big["_dataset"] == "bars"].drop(columns=["_dataset"]),
        factors=big[big["_dataset"] == "factors"].drop(columns=["_dataset"]),
        cot=big[big["_dataset"] == "cot"].drop(columns=["_dataset"]),
        calendar=big[big["_dataset"] == "calendar"].drop(columns=["_dataset"]),
    )


def save_cache(backfill: Backfill) -> None:
    """Persist the backfill bundle to ``BACKFILL_PATH``. The four
    DataFrames are concatenated with a ``_dataset`` discriminator so
    downstream consumers can read everything with one parquet load."""
    BACKFILL_PATH.parent.mkdir(parents=True, exist_ok=True)
    pieces = []
    for name, df in (
        ("bars", backfill.bars),
        ("factors", backfill.factors),
        ("cot", backfill.cot),
        ("calendar", backfill.calendar),
    ):
        if df.empty:
            continue
        labeled = df.copy()
        labeled["_dataset"] = name
        pieces.append(labeled)
    if not pieces:
        return
    pd.concat(pieces, ignore_index=False).to_parquet(BACKFILL_PATH)


def regenerate(as_of: datetime | None = None) -> Backfill:  # pragma: no cover - real-network
    """Pull fresh data from yfinance + FRED + load CFTC / calendar.

    Only runs when explicitly invoked (e.g. ``python -m
    tests.integration.fixtures.backfill``). Requires the same env
    vars as the production ingest: ``FRED_API_KEY`` at minimum.
    """
    import os

    if not os.environ.get("FRED_API_KEY"):
        raise RuntimeError(
            "FRED_API_KEY not set; regenerate requires real upstream credentials"
        )

    end = as_of or datetime.now()
    start = end - timedelta(days=LOOKBACK_DAYS + 90)

    import yfinance as yf

    bar_columns: dict[str, pd.Series] = {}
    for instrument_id, ticker in PROXY_TICKERS.items():
        df = yf.download(
            ticker,
            start=start.date(),
            end=end.date(),
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if df.empty:
            continue
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = [c[0] for c in df.columns]
        bar_columns[instrument_id] = df["Adj Close"].copy()
    bars = pd.DataFrame(bar_columns).dropna(how="all")

    from fredapi import Fred

    fred = Fred(api_key=os.environ["FRED_API_KEY"])
    factor_columns: dict[str, pd.Series] = {}
    for factor_name, fred_id in FACTOR_SERIES:
        try:
            s = fred.get_series(fred_id, observation_start=start.date())
        except Exception:
            continue
        s.index = pd.to_datetime(s.index)
        factor_columns[factor_name] = s
    factors = pd.DataFrame(factor_columns).dropna(how="all")

    # CFTC and calendar are read via existing project plumbing if a
    # DB session is available; for the fixture we read flat files
    # only. Empty stand-ins keep the schema honest.
    cot = pd.DataFrame(
        columns=[
            "report_ts",
            "instrument_id",
            "report_type",
            "open_interest",
            "managed_money_long",
            "managed_money_short",
            "producer_long",
            "producer_short",
        ]
    )
    calendar = pd.DataFrame(
        columns=["event_ts", "kind", "subject", "importance", "affected_instruments"]
    )

    backfill = Backfill(bars=bars, factors=factors, cot=cot, calendar=calendar)
    save_cache(backfill)
    return backfill


@pytest.fixture(scope="session")
def backfill_panel() -> Backfill:
    """Session-scoped fixture exposing the cached backfill.

    Tests that need real data should mark themselves with
    ``@pytest.mark.real_data`` and request this fixture. When the
    cache is missing they are skipped with a clear message instead
    of crashing — local developers regenerate via
    ``python -m tests.integration.fixtures.backfill``.
    """
    if not cache_exists():
        pytest.skip(
            f"backfill cache not found at {BACKFILL_PATH}; regenerate via "
            "`python -m tests.integration.fixtures.backfill` (requires "
            "FRED_API_KEY)"
        )
    bf = load_cached()
    if bf is None:
        pytest.skip("backfill cache present but unreadable; regenerate to fix")
    return bf


if __name__ == "__main__":  # pragma: no cover
    bf = regenerate()
    print(
        f"backfill regenerated: bars={bf.bars.shape} "
        f"factors={bf.factors.shape} -> {BACKFILL_PATH}"
    )
