"""Integration tests for the calendar API + linkage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from macro_trader.calendar.api import events_in_window, is_blackout, next_event
from macro_trader.calendar.events import upsert_event


def _make_event(session, **kwargs):
    base = {
        "kind": "data_release",
        "subject": "US CPI",  # linkage maps to GC, SI
        "importance": "high",
        "region": "US",
        "source": "test",
    }
    base.update(kwargs)
    return upsert_event(session, **base)


@pytest.mark.integration
def test_upsert_is_idempotent(db_session) -> None:
    event_ts = datetime(2026, 6, 1, 12, tzinfo=UTC)
    _make_event(db_session, event_ts=event_ts)
    _make_event(db_session, event_ts=event_ts)  # idempotent
    rows = events_in_window(
        db_session, start=event_ts - timedelta(days=1), end=event_ts + timedelta(days=1)
    )
    assert len(rows) == 1
    assert rows[0].affected_instruments == ["GC", "SI"]


@pytest.mark.integration
def test_events_in_window_filters(db_session) -> None:
    e1 = datetime(2026, 6, 1, tzinfo=UTC)
    e2 = datetime(2026, 6, 5, tzinfo=UTC)
    _make_event(db_session, event_ts=e1, importance="high")
    _make_event(db_session, event_ts=e2, subject="Low importance noise", importance="low")

    high_only = events_in_window(
        db_session,
        start=datetime(2026, 5, 30, tzinfo=UTC),
        end=datetime(2026, 6, 10, tzinfo=UTC),
        importance=["high"],
    )
    assert len(high_only) == 1
    assert high_only[0].event_ts == e1


@pytest.mark.integration
def test_next_event_for_instrument(db_session) -> None:
    e1 = datetime(2026, 6, 1, tzinfo=UTC)
    e2 = datetime(2026, 6, 15, tzinfo=UTC)
    _make_event(db_session, event_ts=e1)
    _make_event(db_session, event_ts=e2, subject="USDA WASDE")  # affects ZC/ZS/ZW

    nxt = next_event(db_session, instrument_id="GC", after=datetime(2026, 5, 1, tzinfo=UTC))
    assert nxt is not None and nxt.event_ts == e1

    nxt = next_event(db_session, instrument_id="ZC", after=datetime(2026, 5, 1, tzinfo=UTC))
    assert nxt is not None and nxt.event_ts == e2


@pytest.mark.integration
def test_is_blackout(db_session) -> None:
    e1 = datetime(2026, 6, 1, 12, tzinfo=UTC)
    _make_event(db_session, event_ts=e1)
    assert is_blackout(
        db_session,
        instrument_id="GC",
        ts=datetime(2026, 6, 1, 6, tzinfo=UTC),  # 6h before event
    )
    assert not is_blackout(
        db_session,
        instrument_id="GC",
        ts=datetime(2026, 6, 3, 0, tzinfo=UTC),  # 36h after, outside default 4h window
    )
    assert not is_blackout(
        db_session,
        instrument_id="CL",  # CPI doesn't affect CL
        ts=datetime(2026, 6, 1, 12, tzinfo=UTC),
    )
