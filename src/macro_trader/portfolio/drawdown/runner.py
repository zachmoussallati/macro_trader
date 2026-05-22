"""Daily drawdown runner.

Two responsibilities:

1. Build today's :class:`EquityCurvePoint` from yesterday's positions
   * today's realised return per instrument; persist to
   ``portfolio.equity_curve``.
2. Read the equity curve + the prior :class:`DrawdownState`; apply
   the staged trigger / auto-release logic; persist the new state
   to ``portfolio.drawdown_state``.

The portfolio runner reads ``portfolio.drawdown_state`` BEFORE
optimisation and multiplies its raw target weights by the current
``effective_scaling_factor`` before persisting positions.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.db.models.market_data import DailyBar
from macro_trader.db.models.portfolio import (
    DrawdownStateRow,
    EquityCurveRow,
    Position,
)
from macro_trader.db.models.system import HeartbeatRow
from macro_trader.logging_setup import get_logger
from macro_trader.portfolio.drawdown.detector import (
    build_equity_curve,
    rolling_window_return,
)
from macro_trader.portfolio.drawdown.gates import (
    DrawdownState,
    GateConfig,
    GateLevelState,
    empty_state,
    evaluate_gate,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def _latest_close(
    session: Session, *, instrument_id: str, on_or_before: datetime
) -> tuple[datetime, float] | None:
    row = session.scalar(
        select(DailyBar)
        .where(DailyBar.instrument_id == instrument_id)
        .where(DailyBar.value_ts <= on_or_before)
        .order_by(desc(DailyBar.value_ts), desc(DailyBar.observation_ts))
        .limit(1)
    )
    if row is None or row.close is None:
        return None
    return row.value_ts, float(row.close)


def compute_realised_portfolio_return(
    session: Session,
    *,
    method_id: str,
    as_of: datetime,
) -> float | None:
    """Realised portfolio return over the last trading day.

    Walks yesterday's persisted positions for ``method_id`` and
    weights each instrument's (today_close - yesterday_close)
    / yesterday_close by its target weight. Returns ``None`` when
    we don't have at least one prior-day position with a usable
    close pair.
    """
    yesterday = as_of - timedelta(days=1)
    yesterday_positions = list(
        session.scalars(
            select(Position)
            .where(Position.method_id == method_id)
            .where(Position.as_of <= yesterday)
            .order_by(desc(Position.as_of))
        )
    )
    if not yesterday_positions:
        return None
    # Latest as_of per instrument.
    latest_by_inst: dict[str, Position] = {}
    for p in yesterday_positions:
        if p.instrument_id not in latest_by_inst:
            latest_by_inst[p.instrument_id] = p
    realised = 0.0
    n_valid = 0
    for inst, p in latest_by_inst.items():
        prior = _latest_close(session, instrument_id=inst, on_or_before=p.as_of)
        today = _latest_close(session, instrument_id=inst, on_or_before=as_of)
        if prior is None or today is None or prior[1] <= 0:
            continue
        if today[0] <= prior[0]:
            continue  # no actual price change since the prior bar
        ret = (today[1] - prior[1]) / prior[1]
        realised += float(p.target_weight) * ret
        n_valid += 1
    if n_valid == 0:
        return None
    return float(realised)


def _previous_nav(session: Session, *, method_id: str, as_of: datetime) -> float:
    row = session.scalar(
        select(EquityCurveRow)
        .where(EquityCurveRow.method_id == method_id)
        .where(EquityCurveRow.as_of < as_of)
        .order_by(desc(EquityCurveRow.as_of))
        .limit(1)
    )
    return float(row.nav) if row is not None else 1.0


def _trailing_daily_returns(
    session: Session, *, method_id: str, as_of: datetime, n_days: int
) -> list[float]:
    rows = list(
        session.scalars(
            select(EquityCurveRow)
            .where(EquityCurveRow.method_id == method_id)
            .where(EquityCurveRow.as_of <= as_of)
            .order_by(desc(EquityCurveRow.as_of))
            .limit(n_days)
        )
    )
    rows.reverse()
    return [float(r.daily_return) if r.daily_return is not None else 0.0 for r in rows]


def _running_peak(session: Session, *, method_id: str) -> float:
    rows = list(
        session.scalars(
            select(EquityCurveRow.peak_nav).where(
                EquityCurveRow.method_id == method_id
            )
        )
    )
    if not rows:
        return 1.0
    return max(float(r) for r in rows if r is not None)


def persist_equity_curve_row(
    session: Session,
    *,
    method_id: str,
    as_of: datetime,
    nav: float,
    daily_return: float,
    cumulative_return: float,
    peak_nav: float,
    drawdown_from_peak: float,
) -> None:
    stmt = pg_insert(EquityCurveRow).values(
        method_id=method_id,
        as_of=as_of,
        nav=nav,
        daily_return=daily_return,
        cumulative_return=cumulative_return,
        peak_nav=peak_nav,
        drawdown_from_peak=drawdown_from_peak,
        equity_metadata={},
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["method_id", "as_of"],
        set_={
            "nav": stmt.excluded.nav,
            "daily_return": stmt.excluded.daily_return,
            "cumulative_return": stmt.excluded.cumulative_return,
            "peak_nav": stmt.excluded.peak_nav,
            "drawdown_from_peak": stmt.excluded.drawdown_from_peak,
        },
    )
    session.execute(stmt)
    session.flush()


def load_drawdown_state(session: Session, *, method_id: str) -> DrawdownState:
    row = session.scalar(
        select(DrawdownStateRow).where(DrawdownStateRow.method_id == method_id)
    )
    if row is None:
        return empty_state(utcnow())

    def _gls(level: str, triggered: datetime | None, release: datetime | None, sf: float) -> GateLevelState:
        return GateLevelState(
            level=("none" if triggered is None else level),  # type: ignore[arg-type]
            triggered_at=triggered,
            release_at=release,
            scaling_factor=sf if triggered is not None else 1.0,
        )

    return DrawdownState(
        level_1=_gls(
            "level_1",
            row.level_1_triggered_at,
            row.level_1_release_at,
            0.5,
        ),
        level_2=_gls(
            "level_2",
            row.level_2_triggered_at,
            row.level_2_release_at,
            0.3,
        ),
        level_3=_gls(
            "level_3",
            row.level_3_triggered_at,
            None,
            0.0,
        ),
        as_of=row.updated_at,
        metadata=dict(row.drawdown_metadata or {}),
    )


def persist_drawdown_state(
    session: Session,
    *,
    method_id: str,
    state: DrawdownState,
) -> None:
    stmt = pg_insert(DrawdownStateRow).values(
        method_id=method_id,
        current_gate_level=state.current_level,
        level_1_triggered_at=state.level_1.triggered_at,
        level_1_release_at=state.level_1.release_at,
        level_2_triggered_at=state.level_2.triggered_at,
        level_2_release_at=state.level_2.release_at,
        level_3_triggered_at=state.level_3.triggered_at,
        effective_scaling_factor=state.effective_scaling_factor,
        updated_at=state.as_of,
        drawdown_metadata=state.metadata,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["method_id"],
        set_={
            "current_gate_level": stmt.excluded.current_gate_level,
            "level_1_triggered_at": stmt.excluded.level_1_triggered_at,
            "level_1_release_at": stmt.excluded.level_1_release_at,
            "level_2_triggered_at": stmt.excluded.level_2_triggered_at,
            "level_2_release_at": stmt.excluded.level_2_release_at,
            "level_3_triggered_at": stmt.excluded.level_3_triggered_at,
            "effective_scaling_factor": stmt.excluded.effective_scaling_factor,
            "updated_at": stmt.excluded.updated_at,
            "drawdown_metadata": stmt.excluded.drawdown_metadata,
        },
    )
    session.execute(stmt)
    session.flush()


def update_drawdown_state(
    session: Session,
    *,
    method_id: str,
    as_of: datetime | None = None,
    config: GateConfig | None = None,
    level_3_manual_release_requested: bool = False,
) -> DrawdownState:
    """End-to-end: realise return -> equity curve row -> gate update.

    Returns the new :class:`DrawdownState` after persistence.
    """
    cfg = config or GateConfig()
    now = as_of or utcnow()

    realised = compute_realised_portfolio_return(
        session, method_id=method_id, as_of=now
    )
    daily_return = float(realised) if realised is not None else 0.0
    prev_nav = _previous_nav(session, method_id=method_id, as_of=now)
    nav = float(prev_nav * (1.0 + daily_return))
    running_peak = max(_running_peak(session, method_id=method_id), nav)
    drawdown = (nav - running_peak) / running_peak if running_peak > 0 else 0.0
    cumulative = nav - 1.0  # starting NAV = 1.0 by convention

    persist_equity_curve_row(
        session,
        method_id=method_id,
        as_of=now,
        nav=nav,
        daily_return=daily_return,
        cumulative_return=cumulative,
        peak_nav=running_peak,
        drawdown_from_peak=drawdown,
    )

    trailing = _trailing_daily_returns(
        session, method_id=method_id, as_of=now, n_days=cfg.level_2_cumulative_window
    )
    rolling_5d = rolling_window_return(
        trailing, window=cfg.level_2_cumulative_window
    )

    prior = load_drawdown_state(session, method_id=method_id)
    new_state = evaluate_gate(
        prior,
        as_of=now,
        daily_return=daily_return if realised is not None else None,
        rolling_5d_return=rolling_5d,
        drawdown_from_peak=drawdown,
        config=cfg,
        level_3_manual_release_requested=level_3_manual_release_requested,
    )
    persist_drawdown_state(session, method_id=method_id, state=new_state)

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="portfolio.drawdown",
            meta={
                "method_id": method_id,
                "level": new_state.current_level,
                "scaling_factor": new_state.effective_scaling_factor,
            },
        )
    )
    session.flush()
    log.info(
        "portfolio.drawdown.update",
        method_id=method_id,
        nav=nav,
        drawdown=drawdown,
        gate=new_state.current_level,
    )
    return new_state


# Re-export the gates-module helpers commonly used by callers so
# `from macro_trader.portfolio.drawdown.runner import build_equity_curve`
# stays a one-line import.
_ = build_equity_curve


__all__ = [
    "compute_realised_portfolio_return",
    "load_drawdown_state",
    "persist_drawdown_state",
    "persist_equity_curve_row",
    "update_drawdown_state",
]
