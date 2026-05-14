"""Date/time helpers. All internal timestamps are UTC-aware."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time as a UTC-aware ``datetime``.

    Use this instead of ``datetime.utcnow()`` (which is naive and deprecated).
    """
    return datetime.now(UTC)


def ensure_aware(dt: datetime) -> datetime:
    """If `dt` is naive, attach UTC; otherwise return unchanged."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def as_utc(dt: datetime) -> datetime:
    """Return `dt` expressed in UTC. Raises if `dt` is naive — we never want to
    silently coerce a naive timestamp from an unknown zone."""
    if dt.tzinfo is None:
        raise ValueError("as_utc() requires a timezone-aware datetime")
    return dt.astimezone(UTC)


def to_iso(dt: datetime) -> str:
    """ISO-8601 with explicit ``+00:00`` for UTC. Naive datetimes raise."""
    return as_utc(dt).isoformat()
