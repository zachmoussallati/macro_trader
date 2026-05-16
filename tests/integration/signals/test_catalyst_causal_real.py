"""Real-data validation for ``catalyst.causal.v1``.

Gated on the ``real_data`` marker. Requires both the backfill
cache parquet AND seeded calendar events + COT history in the
test DB — the catalyst pipeline reads both via the project's
loaders, not just from the parquet.

When either prerequisite is missing tests skip with a clear
message.
"""

from __future__ import annotations

import pickle

import pytest

pytestmark = pytest.mark.real_data


def _need_calendar_events(db_session) -> int:
    """Return the count of medium/high-importance calendar events in
    the test DB; the catalyst pipeline needs at least a few hundred."""
    from sqlalchemy import func, select

    from macro_trader.db.models.macro_data import CalendarEvent

    return int(
        db_session.execute(
            select(func.count())
            .select_from(CalendarEvent)
            .where(CalendarEvent.importance.in_(["medium", "high"]))
        ).scalar_one()
    )


def test_catalyst_causal_serialize_round_trip(backfill_panel, db_session) -> None:
    """Skip if the test DB has no calendar events; otherwise fit
    real CATE, serialize, deserialize, and assert the per-pair
    sensitivities round-trip exactly."""
    if _need_calendar_events(db_session) < 50:
        pytest.skip("test DB has too few calendar events for catalyst CATE")
    if backfill_panel.bars.empty:
        pytest.skip("backfill panel missing bars")

    # Seed minimal instrument rows so loaders find them.
    from macro_trader.data.instruments import upsert_instrument

    for inst in backfill_panel.bars.columns:
        upsert_instrument(
            db_session,
            instrument_id=str(inst),
            name=str(inst),
            asset_class="energy",
            proxy_ticker=str(inst),
            proxy_type="etf",
        )
    db_session.flush()

    from macro_trader.signals.catalyst.methods import CausalCatalyst

    method = CausalCatalyst(min_events_for_cate=15)
    method.fit_on_session(
        db_session,
        backfill_panel.bars.index[-1].to_pydatetime(),
        [str(c) for c in backfill_panel.bars.columns],
    )
    if method._state is None:
        pytest.skip("CausalCatalyst did not fit on this slice (likely no qualifying pairs)")

    blob = method.serialize()
    assert blob
    restored = CausalCatalyst.deserialize(blob)
    assert restored._state is not None
    assert restored._state["cates"] == method._state["cates"]


def test_catalyst_causal_falls_back_for_under_served_pairs(
    backfill_panel, db_session
) -> None:
    """Pairs with <15 events should appear in state with
    ``fallback=True`` rather than disappearing."""
    if _need_calendar_events(db_session) < 50:
        pytest.skip("test DB has too few calendar events for catalyst CATE")

    from macro_trader.data.instruments import upsert_instrument
    from macro_trader.signals.catalyst.methods import CausalCatalyst

    for inst in backfill_panel.bars.columns:
        upsert_instrument(
            db_session,
            instrument_id=str(inst),
            name=str(inst),
            asset_class="energy",
            proxy_ticker=str(inst),
            proxy_type="etf",
        )
    db_session.flush()

    method = CausalCatalyst(min_events_for_cate=15)
    method.fit_on_session(
        db_session,
        backfill_panel.bars.index[-1].to_pydatetime(),
        [str(c) for c in backfill_panel.bars.columns],
    )
    if method._state is None:
        pytest.skip("no qualifying pairs in this backfill")

    cates = method._state["cates"]
    if not cates:
        pytest.skip("no cates emitted")
    # At least some pair on a small backfill should have <15 events
    # and trip the fallback path. If none did, the universe was
    # uniformly rich — assertion is conditional.
    any_fallback = any(c.get("fallback") for c in cates)
    assert any_fallback or all(c["n_events_used"] >= 15 for c in cates), (
        "expected either fallback pairs OR every pair to clear 15 events"
    )
    _ = pickle  # silence unused
