"""EIA release schedule: weekly petroleum (Wed) + weekly nat gas (Thu) + STEO (monthly).

Hardcoded cadence rule that fills the next ``HORIZON_DAYS`` of events.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING

from macro_trader.calendar.events import upsert_event
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


HORIZON_DAYS = 90
PETROLEUM_HOUR_UTC = 15  # ~10:30 ET
NATGAS_HOUR_UTC = 15  # ~10:30 ET


def _next_weekday(after: datetime, weekday: int) -> datetime:
    """Return the next instance of ``weekday`` (Mon=0…Sun=6) at midnight UTC."""
    days_ahead = (weekday - after.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return datetime.combine(
        (after + timedelta(days=days_ahead)).date(),
        time(0, 0),
        tzinfo=UTC,
    )


def seed_eia_schedule(session: Session) -> int:
    """Upsert EIA release events for the next HORIZON_DAYS days."""
    now = utcnow()
    end = now + timedelta(days=HORIZON_DAYS)
    count = 0

    # Weekly Petroleum Status — Wednesdays 10:30 ET (15:30 UTC during DST,
    # so 15:30 is a reasonable rounded UTC).
    cursor = _next_weekday(now, weekday=2)  # Wed
    while cursor < end:
        event_ts = cursor.replace(hour=PETROLEUM_HOUR_UTC, minute=30)
        upsert_event(
            session,
            event_ts=event_ts,
            kind="data_release",
            subject="EIA Weekly Petroleum Status Report",
            region="US",
            importance="high",
            source="eia_schedule",
        )
        count += 1
        cursor += timedelta(days=7)

    # Weekly Natural Gas Storage — Thursdays 10:30 ET.
    cursor = _next_weekday(now, weekday=3)  # Thu
    while cursor < end:
        event_ts = cursor.replace(hour=NATGAS_HOUR_UTC, minute=30)
        upsert_event(
            session,
            event_ts=event_ts,
            kind="data_release",
            subject="EIA Weekly Natural Gas Storage",
            region="US",
            importance="high",
            source="eia_schedule",
        )
        count += 1
        cursor += timedelta(days=7)

    session.commit()
    return count
