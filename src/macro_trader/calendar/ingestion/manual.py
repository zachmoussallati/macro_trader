"""Manual event seeding (OPEC, geopolitical) — hardcoded short list.

Use the ``POST /api/v1/calendar/events`` admin endpoint for ad-hoc adds.
This module only seeds events known at build time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from macro_trader.calendar.events import upsert_event

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# (event_ts, kind, subject, region, importance, source, notes)
_T = tuple[datetime, str, str, str, str, str, str]

MANUAL_EVENTS: tuple[_T, ...] = (
    (
        datetime(2026, 6, 1, 13, 0, tzinfo=UTC),
        "central_bank",
        "OPEC+ Ministerial Meeting",
        "GLOBAL",
        "high",
        "manual_seed",
        "Refresh from opec.org/calendar",
    ),
    (
        datetime(2026, 12, 4, 13, 0, tzinfo=UTC),
        "central_bank",
        "OPEC+ Ministerial Meeting",
        "GLOBAL",
        "high",
        "manual_seed",
        "Refresh from opec.org/calendar",
    ),
)


def seed_manual_events(session: Session) -> int:
    for event_ts, kind, subject, region, importance, source, notes in MANUAL_EVENTS:
        upsert_event(
            session,
            event_ts=event_ts,
            kind=kind,
            subject=subject,
            region=region,
            importance=importance,
            source=source,
            metadata={"notes": notes},
        )
    session.commit()
    return len(MANUAL_EVENTS)
