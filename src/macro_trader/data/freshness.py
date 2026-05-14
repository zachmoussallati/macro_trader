"""Freshness tracking helpers.

The ``system.data_freshness`` table holds one row per ``(source_id,
series_or_table)`` pair. Ingesters call :func:`touch_freshness` on every
run — success or failure — and the consecutive_failures counter rolls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.db.models.system import DataFreshness
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_FREQUENCY_TO_SECONDS: dict[str, int] = {
    "minute": 60,
    "hourly": 3600,
    "daily": 86400,
    "weekly": 7 * 86400,
    "monthly": 31 * 86400,
    "quarterly": 92 * 86400,
}


def _staleness_threshold_seconds(frequency: str) -> int:
    """How long after the expected publication before we call something stale."""
    base = _FREQUENCY_TO_SECONDS.get(frequency, 86400)
    # Allow ~50% slack — providers often publish late.
    return int(base * 1.5)


def upsert_freshness_row(
    session: Session,
    *,
    source_id: str,
    series_or_table: str,
    expected_frequency: str,
    expected_delay_seconds: int | None = None,
) -> DataFreshness:
    """Create the freshness row if missing; return it either way."""
    row = session.get(DataFreshness, (source_id, series_or_table))
    if row is None:
        row = DataFreshness(
            source_id=source_id,
            series_or_table=series_or_table,
            expected_frequency=expected_frequency,
            expected_delay_seconds=expected_delay_seconds,
            updated_at=utcnow(),
        )
        session.add(row)
        session.flush()
    return row


def touch_freshness(
    session: Session,
    *,
    source_id: str,
    series_or_table: str,
    success: bool,
    expected_frequency: str = "daily",
    expected_delay_seconds: int | None = None,
) -> None:
    """Mark a freshness row as freshly attempted.

    ``success=True`` resets ``consecutive_failures`` and updates
    ``last_successful_at``. Either way ``last_attempted_at`` is bumped.
    """
    row = upsert_freshness_row(
        session,
        source_id=source_id,
        series_or_table=series_or_table,
        expected_frequency=expected_frequency,
        expected_delay_seconds=expected_delay_seconds,
    )
    now = utcnow()
    row.last_attempted_at = now
    if success:
        row.last_successful_at = now
        row.consecutive_failures = 0
        row.is_stale = False
    else:
        row.consecutive_failures = (row.consecutive_failures or 0) + 1
    row.updated_at = now
    session.flush()


def mark_stale_if_overdue(session: Session) -> int:
    """Sweep all freshness rows; flip is_stale based on expected frequency.

    Returns the count of rows that just transitioned to stale.
    """
    from sqlalchemy import select

    now = utcnow()
    rows = list(session.scalars(select(DataFreshness)))
    flipped = 0
    for row in rows:
        if row.last_successful_at is None:
            row.is_stale = True
            continue
        threshold = _staleness_threshold_seconds(row.expected_frequency)
        age = (now - row.last_successful_at).total_seconds()
        new_stale = age > threshold
        if new_stale and not row.is_stale:
            flipped += 1
        row.is_stale = new_stale
        row.updated_at = now
    session.flush()
    return flipped
