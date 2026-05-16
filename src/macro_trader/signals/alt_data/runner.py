"""Daily runner for the alt-data signal family.

Three stateless transforms; no refit, no comparator. Each method
runs independently and writes per-instrument rows for whichever
slice of the universe it covers.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from macro_trader.db.models.market_data import Instrument
from macro_trader.db.models.system import HeartbeatRow
from macro_trader.logging_setup import get_logger
from macro_trader.signals.alt_data.methods import (
    EIAStorageSurprise,
    GoogleTrendsSentiment,
    USDAWASDESurprise,
)
from macro_trader.signals.base import SignalInput, SignalMethod
from macro_trader.signals.output import persist_signal_outputs
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_alt_data_methods() -> list[SignalMethod]:
    return [EIAStorageSurprise(), USDAWASDESurprise(), GoogleTrendsSentiment()]


def _active_instruments(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
        )
    )


def run_daily_alt_data(
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
        start=now - timedelta(days=7),
        end=now,
    )

    methods = list(methods) if methods is not None else default_alt_data_methods()
    written: dict[str, int] = {}
    for method in methods:
        outputs = method.compute(sig_input, session)
        written[method.metadata.method_id] = persist_signal_outputs(
            session,
            signal_id=method.metadata.method_id,
            outputs=outputs,
            lineage_id=None,
        )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="signals.alt_data",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("signals.alt_data.daily_run.complete", written=written)
    return written


__all__ = ["default_alt_data_methods", "run_daily_alt_data"]
