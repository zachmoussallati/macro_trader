"""FRED releases → calendar events.

The FRED REST endpoint ``/release/dates`` returns scheduled release dates.
Stage 2 fetches the releases corresponding to the series we ingest (CPI,
PCE, payrolls, retail sales, etc.) and writes one event per future release.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from macro_trader.calendar.events import upsert_event
from macro_trader.logging_setup import get_logger
from macro_trader.utils.dates import ensure_aware, utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


log = get_logger(__name__)


# FRED release IDs → (subject for calendar, importance, region).
# Curated list of US releases relevant to our universe; expand later.
TRACKED_RELEASES: tuple[tuple[int, str, str, str], ...] = (
    (10, "US CPI", "high", "US"),
    (21, "US Industrial Production", "medium", "US"),
    (50, "US Nonfarm Payrolls", "high", "US"),
    (62, "US Retail Sales", "medium", "US"),
    (53, "US GDP", "high", "US"),
    (54, "US PCE", "high", "US"),
)

HORIZON_DAYS = 90


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30), reraise=True)
def _fetch_release_dates(api_key: str, release_id: int) -> list[dict[str, Any]]:
    url = "https://api.stlouisfed.org/fred/release/dates"
    response = httpx.get(
        url,
        params={
            "release_id": release_id,
            "api_key": api_key,
            "file_type": "json",
            "include_release_dates_with_no_data": "true",
        },
        timeout=30.0,
    )
    response.raise_for_status()
    body = response.json()
    return body.get("release_dates") or []


def seed_fred_releases(session: Session, *, api_key: str) -> int:
    """Upsert next ~90 days of FRED releases for tracked release IDs."""
    if not api_key:
        log.info("calendar.fred_releases.skipped", reason="no_api_key")
        return 0
    now = utcnow()
    end = now + timedelta(days=HORIZON_DAYS)
    count = 0
    for release_id, subject, importance, region in TRACKED_RELEASES:
        try:
            dates = _fetch_release_dates(api_key, release_id)
        except Exception as exc:
            log.warning(
                "calendar.fred_releases.fetch_failed",
                release_id=release_id,
                error=str(exc),
            )
            continue
        for entry in dates:
            date_str = entry.get("date")
            if not date_str:
                continue
            try:
                event_ts = ensure_aware(
                    datetime.fromisoformat(date_str).replace(hour=12, tzinfo=UTC)
                )
            except (TypeError, ValueError):
                continue
            if event_ts < now or event_ts > end:
                continue
            upsert_event(
                session,
                event_ts=event_ts,
                kind="data_release",
                subject=subject,
                region=region,
                importance=importance,
                source="fred_releases",
                metadata={"release_id": release_id},
            )
            count += 1
    session.commit()
    return count
