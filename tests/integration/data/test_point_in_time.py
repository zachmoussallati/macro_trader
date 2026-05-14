"""Vintage discipline integration test.

Insert FRED-style vintages for a series, then query at different ``as_of``
points and verify only the correct vintage is returned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from macro_trader.db.models.macro_data import Series, SeriesObservation
from macro_trader.utils.dates import utcnow


@pytest.mark.integration
def test_vintages_filtered_by_as_of(db_session) -> None:
    series_id = "FRED:TEST_VINTAGES"
    now = utcnow()
    db_session.add(
        Series(
            series_id=series_id,
            name="Test vintaged series",
            source="fred",
            frequency="monthly",
            units="x",
            seasonal_adjustment=None,
            category="test",
            affected_instruments=[],
            series_metadata={},
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.flush()

    value_ts = datetime(2024, 1, 1, tzinfo=UTC)
    # Three vintages of the same observation, published 1 month apart.
    vintages = [
        (
            datetime(2024, 2, 1, tzinfo=UTC),
            datetime(2024, 3, 1, tzinfo=UTC) - timedelta(days=1),
            100.0,
            True,
            0,
        ),
        (
            datetime(2024, 3, 1, tzinfo=UTC),
            datetime(2024, 4, 1, tzinfo=UTC) - timedelta(days=1),
            105.0,
            False,
            1,
        ),
        (datetime(2024, 4, 1, tzinfo=UTC), None, 110.0, False, 2),
    ]
    for rt_start, rt_end, value, is_initial, rev in vintages:
        db_session.add(
            SeriesObservation(
                series_id=series_id,
                value_ts=value_ts,
                observation_ts=rt_start,
                value=value,
                realtime_start=rt_start,
                realtime_end=rt_end,
                is_initial=is_initial,
                revision_number=rev,
                source="fred",
            )
        )
    db_session.flush()

    # ----- as_of within the first vintage's window -----
    from sqlalchemy import select

    result = db_session.execute(
        select(SeriesObservation.value).where(
            SeriesObservation.series_id == series_id,
            SeriesObservation.realtime_start <= datetime(2024, 2, 15, tzinfo=UTC),
            SeriesObservation.realtime_end >= datetime(2024, 2, 15, tzinfo=UTC),
        )
    ).scalar_one()
    assert float(result) == 100.0

    # ----- as_of within the second vintage's window -----
    result = db_session.execute(
        select(SeriesObservation.value).where(
            SeriesObservation.series_id == series_id,
            SeriesObservation.realtime_start <= datetime(2024, 3, 15, tzinfo=UTC),
            SeriesObservation.realtime_end >= datetime(2024, 3, 15, tzinfo=UTC),
        )
    ).scalar_one()
    assert float(result) == 105.0

    # ----- as_of in the future (current latest, realtime_end is NULL) -----
    from sqlalchemy import or_

    result = db_session.execute(
        select(SeriesObservation.value).where(
            SeriesObservation.series_id == series_id,
            SeriesObservation.realtime_start <= datetime(2026, 1, 1, tzinfo=UTC),
            or_(
                SeriesObservation.realtime_end.is_(None),
                SeriesObservation.realtime_end >= datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
    ).scalar_one()
    assert float(result) == 110.0
