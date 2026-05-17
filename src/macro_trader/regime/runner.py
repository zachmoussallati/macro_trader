"""Daily runner for the regime classifier family.

Reads cached fitted state (GMM weekly; HMM/MS-VAR quarterly) for
the fitted methods; runs all 5 methods through compute(); persists
one row per (method, value_ts) to ``regime.regime_states``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.db.models.regime import RegimeState
from macro_trader.db.models.system import HeartbeatRow
from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import Method
from macro_trader.methods.comparator import run_comparisons_for_component
from macro_trader.regime.comparator import RegimeClassifierComparator
from macro_trader.regime.methods import (
    BOCPDRegimeClassifier,
    GMMRegimeClassifier,
    HMMRegimeClassifier,
    MSVARRegimeClassifier,
    RegimeInput,
    RegimeOutput,
    RulesRegimeClassifier,
    _hmmlearn_available,
)
from macro_trader.signals.base import SignalInput
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_regime_methods(session: Session | None = None) -> list[Method[Any, Any]]:
    """Build the daily method list. Hydrates fitted state from
    serialized blobs when available; rules + BOCPD are stateless."""
    from macro_trader.regime.refit import (
        load_gmm_state,
        load_hmm_state,
        load_msvar_state,
    )

    methods: list[Method[Any, Any]] = [RulesRegimeClassifier()]
    if session is not None:
        methods.append(load_gmm_state(session) or GMMRegimeClassifier())
        if _hmmlearn_available():
            methods.append(load_hmm_state(session) or HMMRegimeClassifier())
        try:
            methods.append(load_msvar_state(session) or MSVARRegimeClassifier())
        except Exception as exc:  # pragma: no cover - statsmodels env
            log.warning("regime.msvar.unavailable", error=str(exc))
    else:
        methods.extend([GMMRegimeClassifier()])
        if _hmmlearn_available():
            methods.append(HMMRegimeClassifier())
        import contextlib

        with contextlib.suppress(Exception):  # pragma: no cover
            methods.append(MSVARRegimeClassifier())
    methods.append(BOCPDRegimeClassifier())
    return methods


def persist_regime_outputs(
    session: Session, method_id: str, outputs: list[RegimeOutput]
) -> int:
    if not outputs:
        return 0
    rows = [
        {
            "method_id": method_id,
            "value_ts": o.value_ts,
            "observation_ts": o.observation_ts,
            "label": o.label,
            "probability_vector": o.probability_vector,
            "confidence": o.confidence,
            "transition_prob": o.transition_prob,
            "days_in_regime": o.days_in_regime,
            "state_metadata": o.metadata,
        }
        for o in outputs
    ]
    stmt = pg_insert(RegimeState).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["method_id", "value_ts", "observation_ts"],
        set_={
            "label": stmt.excluded.label,
            "probability_vector": stmt.excluded.probability_vector,
            "confidence": stmt.excluded.confidence,
            "transition_prob": stmt.excluded.transition_prob,
            "days_in_regime": stmt.excluded.days_in_regime,
            "state_metadata": stmt.excluded.state_metadata,
        },
    )
    result = session.execute(stmt)
    return int(getattr(result, "rowcount", None) or len(rows))


def run_daily_regime_classification(
    session: Session,
    *,
    methods: Iterable[Method[Any, Any]] | None = None,
    lookback_days: int = 504,
) -> dict[str, int]:
    now = utcnow()
    inp = RegimeInput(as_of=now, lookback_days=lookback_days)
    methods = list(methods) if methods is not None else default_regime_methods(session)

    written: dict[str, int] = {}
    for method in methods:
        # Methods accept RegimeInput; the Method[Any, Any] generic
        # binding here is intentionally loose because the regime
        # family mixes Method subclasses with different InputT/OutputT.
        outputs = method.compute(inp, session)  # type: ignore[attr-defined]
        written[method.metadata.method_id] = persist_regime_outputs(
            session, method.metadata.method_id, outputs
        )

    # Cross-method comparator. The comparator runner expects a
    # SignalInput-shaped data arg; we cast since the regime comparator
    # subclass intentionally narrows the type to RegimeInput downstream.
    sig_input = SignalInput(
        instrument_ids=[],
        as_of=now,
        start=now,
        end=now,
        extras={"session": session, "regime_input": inp},
    )
    run_comparisons_for_component(
        "regime_classifier",
        cast(Any, RegimeClassifierComparator()),
        sig_input,
        period_start=now,
        period_end=now,
        notes="daily regime classifier comparison",
        session=session,
    )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="regime.classification",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("regime.daily_run.complete", written=written)
    return written


__all__ = [
    "default_regime_methods",
    "persist_regime_outputs",
    "run_daily_regime_classification",
]
