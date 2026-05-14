"""USDA WASDE schedule -- monthly, typically the 9th to 12th of each month.

Hardcoded approximate dates with quarterly refresh from
https://usda.gov/oce/commodity/wasde/index.htm.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from macro_trader.calendar.events import upsert_event
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


HORIZON_MONTHS = 12
# WASDE typically releases 12:00 ET (16:00 UTC).
WASDE_HOUR_UTC = 16
# Approximate day-of-month for WASDE releases (refines on real publication).
WASDE_DAY = 11


def seed_usda_schedule(session: Session) -> int:
    """Upsert next ``HORIZON_MONTHS`` WASDE events."""
    now = utcnow()
    count = 0
    year, month = now.year, now.month
    for _ in range(HORIZON_MONTHS):
        event_ts = datetime(year, month, WASDE_DAY, WASDE_HOUR_UTC, 0, tzinfo=UTC)
        if event_ts <= now:
            # Skip past events; move to next month.
            month += 1
            if month > 12:
                month = 1
                year += 1
            continue
        upsert_event(
            session,
            event_ts=event_ts,
            kind="data_release",
            subject="USDA WASDE",
            region="US",
            importance="high",
            source="usda_schedule",
        )
        count += 1
        month += 1
        if month > 12:
            month = 1
            year += 1
    session.commit()
    return count


def seed_drought_monitor_schedule(session: Session) -> int:
    """Drought Monitor releases every Thursday ~08:30 ET."""
    horizon_days = 90
    now = utcnow()
    end = now + timedelta(days=horizon_days)
    cursor = now + timedelta(days=(3 - now.weekday()) % 7)
    count = 0
    while cursor < end:
        event_ts = cursor.replace(hour=13, minute=30, tzinfo=UTC)
        upsert_event(
            session,
            event_ts=event_ts,
            kind="data_release",
            subject="NOAA Drought Monitor",
            region="US",
            importance="medium",
            source="drought_monitor_schedule",
        )
        count += 1
        cursor += timedelta(days=7)
    session.commit()
    return count
