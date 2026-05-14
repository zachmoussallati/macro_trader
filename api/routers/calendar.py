"""Calendar API routes."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from api.deps import AdminDep, SessionDep
from macro_trader.calendar.api import events_in_window, is_blackout, next_event
from macro_trader.calendar.events import upsert_event
from macro_trader.utils.dates import utcnow

router = APIRouter(prefix="/calendar", tags=["calendar"])


class EventOut(BaseModel):
    event_id: uuid.UUID
    event_ts: datetime
    actual_release_ts: datetime | None
    kind: str
    subject: str
    region: str | None
    importance: str
    affected_instruments: list[str]
    affected_series: list[str]
    source: str
    metadata: dict[str, Any]

    @classmethod
    def from_row(cls, row: Any) -> EventOut:
        return cls(
            event_id=row.event_id,
            event_ts=row.event_ts,
            actual_release_ts=row.actual_release_ts,
            kind=row.kind,
            subject=row.subject,
            region=row.region,
            importance=row.importance,
            affected_instruments=list(row.affected_instruments or []),
            affected_series=list(row.affected_series or []),
            source=row.source,
            metadata=dict(row.event_metadata or {}),
        )


class EventCreate(BaseModel):
    event_ts: datetime
    kind: str
    subject: str = Field(min_length=1, max_length=256)
    importance: str = Field(pattern=r"^(low|medium|high)$")
    region: str | None = None
    actual_release_ts: datetime | None = None
    source: str = "manual"
    affected_instruments: list[str] | None = None
    affected_series: list[str] | None = None
    metadata: dict[str, Any] | None = None


@router.get("/events", response_model=list[EventOut])
def list_events(
    session: SessionDep,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    instrument: list[str] | None = Query(default=None),
    importance: list[str] | None = Query(default=None),
    kind: list[str] | None = Query(default=None),
) -> list[EventOut]:
    now = utcnow()
    start = from_ or now - timedelta(days=7)
    end = to or now + timedelta(days=30)
    rows = events_in_window(
        session,
        start=start,
        end=end,
        instruments=instrument,
        importance=importance,
        kinds=kind,
    )
    return [EventOut.from_row(r) for r in rows]


@router.get("/blackout", response_model=dict[str, Any])
def blackout(
    session: SessionDep,
    instrument_id: str = Query(...),
    ts: datetime | None = Query(default=None),
    blackout_hours_before: int = Query(default=24, ge=0, le=168),
    blackout_hours_after: int = Query(default=4, ge=0, le=72),
) -> dict[str, Any]:
    target = ts or utcnow()
    flag = is_blackout(
        session,
        instrument_id=instrument_id,
        ts=target,
        blackout_hours_before=blackout_hours_before,
        blackout_hours_after=blackout_hours_after,
    )
    return {
        "instrument_id": instrument_id,
        "ts": target.isoformat(),
        "is_blackout": flag,
    }


@router.get("/next", response_model=EventOut | None)
def next_event_endpoint(
    session: SessionDep,
    instrument_id: str = Query(...),
    after: datetime | None = Query(default=None),
    importance: list[str] | None = Query(default=None),
) -> EventOut | None:
    target = after or utcnow()
    row = next_event(
        session,
        instrument_id=instrument_id,
        after=target,
        importance=importance,
    )
    if row is None:
        return None
    return EventOut.from_row(row)


@router.post("/events", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    payload: EventCreate,
    session: SessionDep,
    _admin: AdminDep,
) -> EventOut:
    row = upsert_event(
        session,
        event_ts=payload.event_ts,
        kind=payload.kind,
        subject=payload.subject,
        importance=payload.importance,
        region=payload.region,
        actual_release_ts=payload.actual_release_ts,
        source=payload.source,
        metadata=payload.metadata,
        affected_instruments=payload.affected_instruments,
        affected_series=payload.affected_series,
    )
    session.commit()
    return EventOut.from_row(row)
