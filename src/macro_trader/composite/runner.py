"""Daily composite scoring runner (15 minutes after regime classification)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.composite.comparator import CompositeComparator
from macro_trader.composite.methods import (
    BayesianHierarchicalComposite,
    CompositeInput,
    CompositeMethod,
    CompositeOutput,
    LinearComposite,
    _lightgbm_available,
)
from macro_trader.db.models.market_data import Instrument
from macro_trader.db.models.signals import CompositeScore
from macro_trader.db.models.system import HeartbeatRow, MethodRegistryRow
from macro_trader.logging_setup import get_logger
from macro_trader.methods.comparator import run_comparisons_for_component
from macro_trader.signals.designated import resolve_id
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

COMPOSITE_SIGNAL_COMPONENTS: tuple[str, ...] = (
    "trend_signal",
    "carry_signal",
    "value_signal",
    "positioning_signal",
    "dislocation_signal",
    "factor_exposure_signal",
    "catalyst_signal",
    "alt_data_signal",
    "nowcasting_signal",
    "vol_surface_signal",
)


def default_composite_methods(session: Session | None = None) -> list[CompositeMethod]:
    """Build the daily list. Hydrates fitted state when available."""
    from macro_trader.composite.refit import load_bayesian_state, load_gbm_state

    methods: list[CompositeMethod] = [LinearComposite()]
    if session is not None:
        methods.append(load_bayesian_state(session) or BayesianHierarchicalComposite())
        if _lightgbm_available():
            gbm = load_gbm_state(session)
            if gbm is not None:
                methods.append(gbm)
    else:
        methods.append(BayesianHierarchicalComposite())
    return methods


def _resolve_designated_signal_methods(session: Session) -> list[str]:
    """Map each signal component to its production-designated method id.

    Returns whichever subset actually has registered methods (when
    Stage 5 isn't fully bootstrapped, some components may be missing).
    """
    out: list[str] = []
    for component in COMPOSITE_SIGNAL_COMPONENTS:
        method_id = resolve_id(component)
        if method_id is None:
            log.info(
                "composite.runner.no_designated_method", component=component
            )
            continue
        # Verify it's actually registered in the DB (defensive: registry
        # may have a stale in-memory entry from a prior test).
        present = session.scalar(
            select(MethodRegistryRow.method_id).where(
                MethodRegistryRow.method_id == method_id
            )
        )
        if present is not None:
            out.append(method_id)
    return out


def _active_instruments(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
        )
    )


def persist_composite_outputs(
    session: Session, method_id: str, outputs: list[CompositeOutput]
) -> int:
    if not outputs:
        return 0
    rows = [
        {
            "method_id": method_id,
            "instrument_id": o.instrument_id,
            "value_ts": o.value_ts,
            "observation_ts": o.observation_ts,
            "raw_score": o.raw_score,
            "score": o.score,
            "confidence": o.confidence,
            "regime_label": o.regime_label,
            "n_signals_used": o.n_signals_used,
            "composite_metadata": o.metadata,
        }
        for o in outputs
    ]
    stmt = pg_insert(CompositeScore).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["method_id", "instrument_id", "value_ts", "observation_ts"],
        set_={
            "raw_score": stmt.excluded.raw_score,
            "score": stmt.excluded.score,
            "confidence": stmt.excluded.confidence,
            "regime_label": stmt.excluded.regime_label,
            "n_signals_used": stmt.excluded.n_signals_used,
            "composite_metadata": stmt.excluded.composite_metadata,
        },
    )
    result = session.execute(stmt)
    return int(getattr(result, "rowcount", None) or len(rows))


def run_daily_composite(
    session: Session,
    *,
    instruments: list[str] | None = None,
    methods: Iterable[CompositeMethod] | None = None,
    window_days: int = 7,
    as_of: datetime | None = None,
) -> dict[str, int]:
    """Daily composite job (Dagster 23:45 UTC).

    Runs every registered composite method over the trailing
    ``window_days``; persists rows to ``signals.composite_scores``;
    runs the cross-method comparator.
    """
    instruments = instruments or _active_instruments(session)
    now = as_of or utcnow()
    designated = _resolve_designated_signal_methods(session)
    regime_method_id = resolve_id("regime_classifier") or "regime.rules.v1"

    inp = CompositeInput(
        instrument_ids=instruments,
        as_of=now,
        start=now - timedelta(days=window_days),
        end=now,
        regime_method_id=regime_method_id,
        designated_signal_method_ids=designated,
    )

    methods = list(methods) if methods is not None else default_composite_methods(session)
    lineage_id: uuid.UUID | None = None
    written: dict[str, int] = {}
    for method in methods:
        outputs = method.compute(inp, session)
        written[method.metadata.method_id] = persist_composite_outputs(
            session, method.metadata.method_id, outputs
        )
        _ = lineage_id  # reserved for the persistence module's lineage hook

    # Cross-method comparator. We pass the CompositeInput as the data
    # arg; the comparator's _compute_metrics uses output_a/output_b
    # only, but its base class needs a non-None data for the
    # signature contract.
    run_comparisons_for_component(
        "composite_score",
        cast(Any, CompositeComparator()),
        inp,
        period_start=inp.start,
        period_end=inp.end,
        notes="daily composite comparison",
        session=session,
    )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="composite.daily",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("composite.daily_run.complete", written=written)
    return written


__all__ = [
    "COMPOSITE_SIGNAL_COMPONENTS",
    "default_composite_methods",
    "persist_composite_outputs",
    "run_daily_composite",
]
