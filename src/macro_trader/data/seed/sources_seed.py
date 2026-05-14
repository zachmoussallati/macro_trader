"""Seed the ``system.data_sources`` registry + initial freshness rows.

Idempotent — safe to re-run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.data.freshness import upsert_freshness_row
from macro_trader.db.models.system import DataSource
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# (source_id, name, base_url, requires_auth, rate_limit_notes)
SOURCES: tuple[tuple[str, str, str, bool, str], ...] = (
    (
        "fred",
        "Federal Reserve Economic Data (FRED + ALFRED)",
        "https://api.stlouisfed.org/fred",
        True,
        "120 requests/minute soft limit; key from https://fred.stlouisfed.org/docs/api/api_key.html",
    ),
    (
        "yfinance",
        "Yahoo Finance (via yfinance)",
        "https://query2.finance.yahoo.com",
        False,
        "Unofficial; back off aggressively on 429. ~60 req/min sustainable.",
    ),
    (
        "cftc",
        "CFTC Commitments of Traders",
        "https://www.cftc.gov/dea/newcot",
        False,
        "Weekly CSV download; published Fridays ~3:30 ET. No documented rate limit.",
    ),
    (
        "eia",
        "U.S. Energy Information Administration",
        "https://api.eia.gov",
        True,
        "5000 req/hour; key from https://www.eia.gov/opendata/register.php",
    ),
    (
        "usda",
        "USDA NASS Quick Stats",
        "https://quickstats.nass.usda.gov/api",
        True,
        "No published rate limit; key from https://quickstats.nass.usda.gov/api",
    ),
    (
        "noaa",
        "NOAA Climate Data Online",
        "https://www.ncei.noaa.gov/cdo-web/api/v2",
        True,
        "5 req/sec, 10000 req/day; token from https://www.ncdc.noaa.gov/cdo-web/token",
    ),
    (
        "google_trends",
        "Google Trends (via pytrends)",
        "https://trends.google.com",
        False,
        "Heavy throttling; long backoff on 429. Use sparingly.",
    ),
)


# (source_id, series_or_table, expected_frequency)
FRESHNESS_TARGETS: tuple[tuple[str, str, str], ...] = (
    ("fred", "fred.series_observations", "daily"),
    ("yfinance", "market_data.daily_bars", "daily"),
    ("cftc", "positioning.cot_weekly", "weekly"),
    ("eia", "alt_data.eia_inventory", "weekly"),
    ("usda", "alt_data.usda_reports", "monthly"),
    ("noaa", "alt_data.weather_data", "daily"),
    ("google_trends", "alt_data.google_trends", "daily"),
)


def seed(session: Session) -> int:
    """Upsert sources + freshness rows. Returns the count of source rows."""
    now = utcnow()
    for source_id, name, base_url, requires_auth, rate_limit_notes in SOURCES:
        row = session.get(DataSource, source_id)
        if row is None:
            session.add(
                DataSource(
                    source_id=source_id,
                    name=name,
                    base_url=base_url,
                    requires_auth=requires_auth,
                    rate_limit_notes=rate_limit_notes,
                    is_healthy=True,
                    source_metadata={},
                    created_at=now,
                )
            )
        else:
            row.name = name
            row.base_url = base_url
            row.requires_auth = requires_auth
            row.rate_limit_notes = rate_limit_notes
    session.flush()

    for source_id, series_or_table, expected_frequency in FRESHNESS_TARGETS:
        upsert_freshness_row(
            session,
            source_id=source_id,
            series_or_table=series_or_table,
            expected_frequency=expected_frequency,
        )

    session.commit()
    return len(SOURCES)
