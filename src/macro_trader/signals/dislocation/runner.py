"""Daily runner for the cross-asset dislocation signal family.

Stage 4B routes the daily run through the weekly-refitted models when
present:

1. Look up serialized fitted state for each method via
   ``signals.dislocation.refit.load_pca_state`` /
   ``load_dfm_state``.
2. If state is present, the method's ``compute()`` skips the fit step
   and uses the cached PCA / DFM directly.
3. If no state exists yet (first run, or last week's refit failed),
   the method falls back to fitting on the daily window — logged as
   ``signals.dislocation.<method>.fallback_fit``.

The dedicated weekly refit asset is wired separately
(``orchestration/assets/signals.py:dislocation_models_refit``).
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
from macro_trader.signals.dislocation.refit import load_dfm_state, load_pca_state
from macro_trader.signals.output import persist_signal_outputs
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_dislocation_methods(session: Session | None = None) -> list[SignalMethod]:
    """Build the daily method list, populating fitted state from the
    weekly-refit blob when available."""
    methods: list[SignalMethod] = []
    if session is not None:
        pca = load_pca_state(session)
        if pca is not None:
            methods.append(pca)
        else:
            methods.append(PCADislocation())
        dfm = load_dfm_state(session)
        if dfm is not None:
            methods.append(dfm)
        else:
            methods.append(DynamicFactorModel())
    else:
        methods.extend([PCADislocation(), DynamicFactorModel()])
    return methods


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

    methods = list(methods) if methods is not None else default_dislocation_methods(session)
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
