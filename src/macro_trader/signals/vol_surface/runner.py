"""Daily runner for the vol-surface signal family."""

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
from macro_trader.signals.vol_surface.comparator import VolSurfaceSignalComparator
from macro_trader.signals.vol_surface.methods import (
    RawVolSurface,
    SVIVolSurface,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_vol_surface_methods() -> list[SignalMethod]:
    return [RawVolSurface(), SVIVolSurface()]


def _active_instruments(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
        )
    )


def run_daily_vol_surface(
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
        start=now - timedelta(days=1),
        end=now,
    )

    methods = list(methods) if methods is not None else default_vol_surface_methods()
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
        "vol_surface_signal",
        VolSurfaceSignalComparator(),
        sig_with_session,
        period_start=sig_input.start,
        period_end=sig_input.end,
        notes="daily vol surface signal comparison",
        session=session,
    )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="signals.vol_surface",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("signals.vol_surface.daily_run.complete", written=written)
    return written


__all__ = ["default_vol_surface_methods", "run_daily_vol_surface"]
