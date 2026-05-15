"""Daily runner for the positioning signal family.

Positioning signals only update when new CFTC COT data lands (Fridays
post-publication). The asset still runs daily because:

- The window of emitted ``signal_values`` rolls forward each day even
  when the underlying COT row is unchanged — keeps query patterns
  uniform with the trend / value runners.
- Each row's ``metadata.is_fresh_data`` flag distinguishes "new COT data
  arrived this run" from "today's row mirrors yesterday".

The runner calls :func:`run_comparisons_for_component` (Phase 1a) so it
automatically picks up any future shadows added to the registry.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from macro_trader.db.models.market_data import Instrument
from macro_trader.db.models.system import HeartbeatRow
from macro_trader.logging_setup import get_logger
from macro_trader.methods.comparator import run_comparisons_for_component
from macro_trader.signals.base import SignalInput, SignalMethod
from macro_trader.signals.output import persist_signal_outputs
from macro_trader.signals.positioning.comparator import PositioningSignalComparator
from macro_trader.signals.positioning.methods import CotCommercial, CotZScore
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_positioning_methods() -> list[SignalMethod]:
    return [CotZScore(), CotCommercial()]


def _active_instruments(session: Session) -> list[str]:
    rows = session.scalars(
        select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
    ).all()
    return list(rows)


def run_daily_positioning(
    session: Session,
    *,
    instruments: list[str] | None = None,
    window_days: int = 28,
    methods: Iterable[SignalMethod] | None = None,
) -> dict[str, int]:
    """Run both positioning methods over the trailing ``window_days``.

    ``window_days`` defaults to 28 — four weekly reports' worth — so a
    daily run keeps the most recent month of values fresh in the
    ``signal_values`` table.
    """
    instruments = instruments or _active_instruments(session)
    now = utcnow()
    sig_input = SignalInput(
        instrument_ids=instruments,
        as_of=now,
        start=now - timedelta(days=window_days),
        end=now,
    )

    methods = list(methods) if methods is not None else default_positioning_methods()
    written: dict[str, int] = {}
    for method in methods:
        outputs = method.compute(sig_input, session)
        written[method.metadata.method_id] = persist_signal_outputs(
            session,
            signal_id=method.metadata.method_id,
            outputs=outputs,
            lineage_id=None,
        )

    sig_with_session = SignalInput(
        instrument_ids=sig_input.instrument_ids,
        as_of=sig_input.as_of,
        start=sig_input.start,
        end=sig_input.end,
        extras={"session": session},
    )
    run_comparisons_for_component(
        "positioning_signal",
        PositioningSignalComparator(),
        sig_with_session,
        period_start=sig_input.start,
        period_end=sig_input.end,
        notes="daily positioning signal comparison",
        session=session,
    )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="signals.positioning",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("signals.positioning.daily_run.complete", written=written)
    return written


__all__ = ["default_positioning_methods", "run_daily_positioning"]
