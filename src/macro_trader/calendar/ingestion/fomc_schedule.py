"""FOMC meeting calendar — hardcoded with quarterly refresh.

Refresh procedure: pull the latest published schedule from
https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm and update
the ``FOMC_DATES`` list. ``seed_fomc_schedule`` is idempotent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from macro_trader.calendar.events import upsert_event

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Two-day FOMC meetings end on day 2 at 14:00 ET (18:00 UTC). We store the
# decision time. Update when Fed publishes new schedule.
FOMC_DATES: tuple[datetime, ...] = (
    # 2026 schedule (placeholder — refresh from Fed when 2026/2027 published)
    datetime(2026, 1, 28, 19, 0, tzinfo=UTC),
    datetime(2026, 3, 18, 18, 0, tzinfo=UTC),
    datetime(2026, 4, 29, 18, 0, tzinfo=UTC),
    datetime(2026, 6, 17, 18, 0, tzinfo=UTC),
    datetime(2026, 7, 29, 18, 0, tzinfo=UTC),
    datetime(2026, 9, 16, 18, 0, tzinfo=UTC),
    datetime(2026, 10, 28, 18, 0, tzinfo=UTC),
    datetime(2026, 12, 9, 19, 0, tzinfo=UTC),
)


def seed_fomc_schedule(session: Session) -> int:
    """Upsert FOMC meeting events. Returns the count touched."""
    for event_ts in FOMC_DATES:
        upsert_event(
            session,
            event_ts=event_ts,
            kind="central_bank",
            subject="FOMC Decision",
            region="US",
            importance="high",
            source="fomc_schedule",
            metadata={"schedule_source": "federalreserve.gov"},
        )
    session.commit()
    return len(FOMC_DATES)
