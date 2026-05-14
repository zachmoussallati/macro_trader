"""Calendar query API: ``events_in_window``, ``next_event``, ``is_blackout``."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, select

from macro_trader.db.models.macro_data import CalendarEvent

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def events_in_window(
    session: Session,
    *,
    start: datetime,
    end: datetime,
    instruments: list[str] | None = None,
    importance: list[str] | None = None,
    kinds: list[str] | None = None,
) -> list[CalendarEvent]:
    """Return events with ``start <= event_ts < end``.

    ``instruments`` filter intersects against the ``affected_instruments``
    array; ``importance`` and ``kinds`` are simple IN filters.
    """
    stmt = (
        select(CalendarEvent)
        .where(and_(CalendarEvent.event_ts >= start, CalendarEvent.event_ts < end))
        .order_by(CalendarEvent.event_ts)
    )
    if importance:
        stmt = stmt.where(CalendarEvent.importance.in_(importance))
    if kinds:
        stmt = stmt.where(CalendarEvent.kind.in_(kinds))
    if instruments:
        stmt = stmt.where(CalendarEvent.affected_instruments.overlap(instruments))
    return list(session.scalars(stmt))


def next_event(
    session: Session,
    *,
    instrument_id: str,
    after: datetime,
    importance: list[str] | None = None,
) -> CalendarEvent | None:
    """Earliest event for the instrument occurring after ``after``."""
    stmt = (
        select(CalendarEvent)
        .where(CalendarEvent.event_ts > after)
        .where(CalendarEvent.affected_instruments.overlap([instrument_id]))
        .order_by(CalendarEvent.event_ts)
        .limit(1)
    )
    if importance:
        stmt = stmt.where(CalendarEvent.importance.in_(importance))
    return session.execute(stmt).scalar_one_or_none()


def is_blackout(
    session: Session,
    *,
    instrument_id: str,
    ts: datetime,
    blackout_hours_before: int = 24,
    blackout_hours_after: int = 4,
    importance: list[str] | None = None,
) -> bool:
    """True if ``ts`` falls inside the blackout window of any high-importance
    event affecting ``instrument_id``.

    Default window: 24 hours before through 4 hours after the event. Used
    by execution (Stage 12) to gate order submission and by signals
    (Stage 4) to mask data around catalyst windows.
    """
    if importance is None:
        importance = ["high"]
    window_start = ts - timedelta(hours=blackout_hours_after)
    window_end = ts + timedelta(hours=blackout_hours_before)
    stmt = (
        select(CalendarEvent.event_id)
        .where(and_(CalendarEvent.event_ts >= window_start, CalendarEvent.event_ts <= window_end))
        .where(CalendarEvent.affected_instruments.overlap([instrument_id]))
        .where(CalendarEvent.importance.in_(importance))
        .limit(1)
    )
    return session.execute(stmt).first() is not None
