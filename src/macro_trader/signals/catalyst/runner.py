"""Daily runner for the catalyst sensitivity family."""

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
from macro_trader.signals.catalyst.comparator import CatalystSignalComparator
from macro_trader.signals.catalyst.methods import (
    EventStudyCatalyst,
    _econml_available,
)
from macro_trader.signals.catalyst.refit import (
    load_causal_state,
    load_event_study_state,
)
from macro_trader.signals.output import persist_signal_outputs
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_catalyst_methods(session: Session | None = None) -> list[SignalMethod]:
    methods: list[SignalMethod] = []
    if session is not None:
        methods.append(load_event_study_state(session) or EventStudyCatalyst())
        if _econml_available():
            cf = load_causal_state(session)
            if cf is not None:
                methods.append(cf)
    else:
        methods.append(EventStudyCatalyst())
    return methods


def _active_instruments(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
        )
    )


def run_daily_catalyst(
    session: Session,
    *,
    instruments: list[str] | None = None,
    methods: Iterable[SignalMethod] | None = None,
) -> dict[str, int]:
    instruments = instruments or _active_instruments(session)
    now = utcnow()
    sig_input = SignalInput(
        instrument_ids=instruments,
        as_of=now,
        start=now,
        end=now + timedelta(days=10),
    )

    methods = list(methods) if methods is not None else default_catalyst_methods(session)
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
        "catalyst_signal",
        CatalystSignalComparator(),
        sig_with_session,
        period_start=sig_input.start,
        period_end=sig_input.end,
        notes="daily catalyst signal comparison",
        session=session,
    )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="signals.catalyst",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("signals.catalyst.daily_run.complete", written=written)
    return written


__all__ = ["default_catalyst_methods", "run_daily_catalyst"]
