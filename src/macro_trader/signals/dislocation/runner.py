"""Daily runner for the cross-asset dislocation signal family.

Both methods fit on each call for simplicity in Stage 4A. The PCA fit
on a 13-instrument x 252-day panel is sub-second; DFM fit is ~30-60s on
larger samples — acceptable for a daily asset, less so for an interactive
query path. A weekly refit asset that persists fitted state via
``system.methods_registry.serialized_blob`` is documented in
``notes/stage_4a/tradeoffs.md`` as the natural next step.
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
from macro_trader.signals.dislocation.comparator import DislocationSignalComparator
from macro_trader.signals.dislocation.methods import (
    DynamicFactorModel,
    PCADislocation,
)
from macro_trader.signals.output import persist_signal_outputs
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_dislocation_methods() -> list[SignalMethod]:
    return [PCADislocation(), DynamicFactorModel()]


def _active_instruments(session: Session) -> list[str]:
    rows = session.scalars(
        select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
    ).all()
    return list(rows)


def run_daily_dislocation(
    session: Session,
    *,
    instruments: list[str] | None = None,
    window_days: int = 14,
    methods: Iterable[SignalMethod] | None = None,
) -> dict[str, int]:
    """Run both dislocation methods over the trailing ``window_days``.

    DFM may return 0 outputs on small samples (its fit fails); we log
    and persist whatever PCA produces.
    """
    instruments = instruments or _active_instruments(session)
    now = utcnow()
    sig_input = SignalInput(
        instrument_ids=instruments,
        as_of=now,
        start=now - timedelta(days=window_days),
        end=now,
    )

    methods = list(methods) if methods is not None else default_dislocation_methods()
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
        "dislocation_signal",
        DislocationSignalComparator(),
        sig_with_session,
        period_start=sig_input.start,
        period_end=sig_input.end,
        notes="daily dislocation signal comparison",
        session=session,
    )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="signals.dislocation",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("signals.dislocation.daily_run.complete", written=written)
    return written


__all__ = ["default_dislocation_methods", "run_daily_dislocation"]
