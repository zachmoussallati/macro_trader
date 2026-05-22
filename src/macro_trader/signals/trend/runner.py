"""Daily run logic for the trend family."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from macro_trader.db.models.market_data import Instrument
from macro_trader.db.models.regime import RegimeState
from macro_trader.db.models.system import HeartbeatRow
from macro_trader.logging_setup import get_logger
from macro_trader.methods.comparator import run_comparisons_for_component
from macro_trader.signals.base import SignalInput, SignalMethod
from macro_trader.signals.designated import resolve_id
from macro_trader.signals.output import persist_signal_outputs
from macro_trader.signals.trend.comparator import TrendSignalComparator
from macro_trader.signals.trend.methods import (
    HPFilterTrend,
    SMACrossoverLong,
    SMACrossoverMedium,
    SMACrossoverShort,
    TrendEnsemble,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_trend_methods() -> list[SignalMethod]:
    """Concrete methods that get registered + run daily by the trend family."""
    s = SMACrossoverShort()
    m = SMACrossoverMedium()
    l = SMACrossoverLong()  # noqa: E741 — local var, not a stylistic preference for `l`
    ensemble = TrendEnsemble(components=[s, m, l])
    return [s, m, l, ensemble, HPFilterTrend()]


def _active_instruments(session: Session) -> list[str]:
    rows = session.scalars(
        select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
    ).all()
    return list(rows)


def _latest_regime_label(session: Session) -> str | None:
    """Latest regime label from the production-designated regime
    classifier (e.g. ``regime.rules.v1``).

    Returns ``None`` when:
    - no regime classifier is designated (Stage 6 not deployed yet),
    - the designated classifier hasn't run yet (no rows in
      ``regime.regime_states``).

    Callers should treat ``None`` as "fall back to Stage 3 equal
    weights" rather than failing.
    """
    method_id = resolve_id("regime_classifier")
    if method_id is None:
        return None
    row = session.scalar(
        select(RegimeState)
        .where(RegimeState.method_id == method_id)
        .order_by(RegimeState.value_ts.desc(), RegimeState.observation_ts.desc())
        .limit(1)
    )
    return row.label if row is not None else None


def run_daily_trend(
    session: Session,
    *,
    instruments: list[str] | None = None,
    window_days: int = 14,
    methods: Iterable[SignalMethod] | None = None,
) -> dict[str, int]:
    """Run all trend methods for the trailing ``window_days``.

    Returns a per-method count of rows written. Idempotent on the
    natural key thanks to the UPSERT in :func:`persist_signal_outputs`.
    The window defaults to 14 days so a daily run keeps the most recent
    history fresh; longer backfills can pass an explicit window.
    """
    instruments = instruments or _active_instruments(session)
    now = utcnow()
    regime_label = _latest_regime_label(session)
    sig_input = SignalInput(
        instrument_ids=instruments,
        as_of=now,
        start=now - timedelta(days=window_days),
        end=now,
        regime_state=regime_label,
    )

    methods = list(methods) if methods is not None else default_trend_methods()
    written: dict[str, int] = {}
    lineage_id: uuid.UUID | None = None

    for method in methods:
        outputs = method.compute(sig_input, session)
        rows = persist_signal_outputs(
            session,
            signal_id=method.metadata.method_id,
            outputs=outputs,
            lineage_id=lineage_id,
        )
        written[method.metadata.method_id] = rows

    # Comparator: every SHADOW vs the registry's reference method.
    # In Stage 3 that's just (trend.ensemble.v1 BASELINE, trend.hp_filter.v1
    # SHADOW); the loop scales when Stage 4+ adds more shadows.
    sig_with_session = SignalInput(
        instrument_ids=sig_input.instrument_ids,
        as_of=sig_input.as_of,
        start=sig_input.start,
        end=sig_input.end,
        regime_state=sig_input.regime_state,
        extras={"session": session},
    )
    run_comparisons_for_component(
        "trend_signal",
        TrendSignalComparator(),
        sig_with_session,
        period_start=sig_input.start,
        period_end=sig_input.end,
        notes="daily trend signal comparison",
        session=session,
    )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="signals.trend",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("signals.trend.daily_run.complete", written=written)
    return written


def safe_run_daily_trend(
    session: Session, *, instruments: list[str] | None = None
) -> dict[str, int]:
    """Wrap :func:`run_daily_trend` with broad exception logging.

    Used by the Dagster asset wrapper so a per-method bug doesn't abort
    the entire family; the asset still raises if zero rows are written
    across all methods (caught by the asset's error reporting).
    """
    try:
        return run_daily_trend(session, instruments=instruments)
    except Exception as exc:
        log.error("signals.trend.daily_run.failed", error=str(exc), exc_info=True)
        raise


__all__ = ["default_trend_methods", "run_daily_trend", "safe_run_daily_trend"]
