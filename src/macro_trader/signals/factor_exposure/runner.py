"""Daily runner for the factor exposure family.

Reads cached fitted state from
``system.methods_registry.serialized_blob`` (populated weekly by
``factor_exposure_models_refit``); falls back to fitting on the daily
window when no state is available.

The Causal Forest method is only included when the ``[ml]`` extra is
installed and a fitted blob exists. When EconML is missing, the
method silently drops out of the daily run — the OLS baseline + RF
shadow still produce signals.
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
from macro_trader.signals.factor_exposure.comparator import (
    FactorExposureComparator,
)
from macro_trader.signals.factor_exposure.methods import (
    OLSFactorExposure,
    RandomForestFactorExposure,
    _econml_available,
)
from macro_trader.signals.factor_exposure.refit import (
    load_causal_forest_state,
    load_ols_state,
    load_rf_state,
)
from macro_trader.signals.output import persist_signal_outputs
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_factor_exposure_methods(
    session: Session | None = None,
) -> list[SignalMethod]:
    methods: list[SignalMethod] = []
    if session is not None:
        methods.append(load_ols_state(session) or OLSFactorExposure())
        methods.append(load_rf_state(session) or RandomForestFactorExposure())
        if _econml_available():
            cf = load_causal_forest_state(session)
            if cf is not None:
                methods.append(cf)  # type: ignore[arg-type]
    else:
        methods.extend([OLSFactorExposure(), RandomForestFactorExposure()])
    return methods


def _active_instruments(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
        )
    )


def run_daily_factor_exposure(
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

    methods = list(methods) if methods is not None else default_factor_exposure_methods(session)
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
        "factor_exposure_signal",
        FactorExposureComparator(),
        sig_with_session,
        period_start=sig_input.start,
        period_end=sig_input.end,
        notes="daily factor exposure signal comparison",
        session=session,
    )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="signals.factor_exposure",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("signals.factor_exposure.daily_run.complete", written=written)
    return written


__all__ = ["default_factor_exposure_methods", "run_daily_factor_exposure"]
