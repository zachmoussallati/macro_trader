"""Daily runner for the value family."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from macro_trader.db.models.market_data import Instrument
from macro_trader.db.models.system import HeartbeatRow
from macro_trader.logging_setup import get_logger
from macro_trader.signals.base import SignalInput, SignalMethod
from macro_trader.signals.output import persist_signal_outputs
from macro_trader.signals.value.comparator import ValueSignalComparator
from macro_trader.signals.value.methods import (
    CrossSectionalValue,
    ZScoreValue,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_value_methods() -> list[SignalMethod]:
    return [ZScoreValue(), CrossSectionalValue()]


def _active_instruments(session: Session) -> list[str]:
    rows = session.scalars(
        select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
    ).all()
    return list(rows)


def run_daily_value(
    session: Session,
    *,
    instruments: list[str] | None = None,
    window_days: int = 14,
    methods: Iterable[SignalMethod] | None = None,
) -> dict[str, int]:
    instruments = instruments or _active_instruments(session)
    now = utcnow()
    sig_input = SignalInput(
        instrument_ids=instruments,
        as_of=now,
        start=now - timedelta(days=window_days),
        end=now,
    )
    methods = list(methods) if methods is not None else default_value_methods()
    written: dict[str, int] = {}
    method_map = {m.metadata.method_id: m for m in methods}
    for method in methods:
        outputs = method.compute(sig_input, session)
        written[method.metadata.method_id] = persist_signal_outputs(
            session,
            signal_id=method.metadata.method_id,
            outputs=outputs,
            lineage_id=None,
        )

    baseline = method_map.get("value.zscore.v1")
    shadow = method_map.get("value.cross_sectional.v1")
    if baseline is not None and shadow is not None:
        comparator = ValueSignalComparator()
        sig_with_session = SignalInput(
            instrument_ids=sig_input.instrument_ids,
            as_of=sig_input.as_of,
            start=sig_input.start,
            end=sig_input.end,
            extras={"session": session},
        )
        comparator.compare(
            baseline,
            shadow,
            sig_with_session,
            period_start=sig_input.start,
            period_end=sig_input.end,
            notes="daily value signal comparison",
            session=session,
        )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="signals.value",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("signals.value.daily_run.complete", written=written)
    return written


__all__ = ["default_value_methods", "run_daily_value"]
