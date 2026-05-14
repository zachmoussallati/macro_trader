"""Event row creation + persistence (idempotent on natural key)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, select

from macro_trader.calendar.linkage import (
    instruments_for_subject,
    series_for_subject,
)
from macro_trader.db.models.macro_data import CalendarEvent
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def upsert_event(
    session: Session,
    *,
    event_ts: datetime,
    kind: str,
    subject: str,
    importance: str,
    region: str | None = None,
    actual_release_ts: datetime | None = None,
    source: str = "manual",
    metadata: dict[str, Any] | None = None,
    affected_instruments: list[str] | None = None,
    affected_series: list[str] | None = None,
) -> CalendarEvent:
    """Insert or update a calendar event.

    Natural key for dedup is ``(event_ts, kind, subject, source)``. If a
    row with that key exists, we update mutable fields (importance, links,
    metadata, actual_release_ts) and leave timestamps alone.
    """
    if affected_instruments is None:
        affected_instruments = instruments_for_subject(subject)
    if affected_series is None:
        affected_series = series_for_subject(subject)

    existing = session.execute(
        select(CalendarEvent).where(
            and_(
                CalendarEvent.event_ts == event_ts,
                CalendarEvent.kind == kind,
                CalendarEvent.subject == subject,
                CalendarEvent.source == source,
            )
        )
    ).scalar_one_or_none()

    now = utcnow()
    if existing is None:
        row = CalendarEvent(
            event_ts=event_ts,
            actual_release_ts=actual_release_ts,
            kind=kind,
            subject=subject,
            region=region,
            importance=importance,
            affected_instruments=list(affected_instruments),
            affected_series=list(affected_series),
            source=source,
            event_metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row

    existing.actual_release_ts = actual_release_ts or existing.actual_release_ts
    existing.region = region or existing.region
    existing.importance = importance
    existing.affected_instruments = list(affected_instruments)
    existing.affected_series = list(affected_series)
    if metadata is not None:
        merged = dict(existing.event_metadata or {})
        merged.update(metadata)
        existing.event_metadata = merged
    existing.updated_at = now
    session.flush()
    return existing
