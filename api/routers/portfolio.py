"""Portfolio API (Stage 8).

8 endpoints under ``/api/v1/portfolio/``:

- ``GET /methods``                          list registered portfolio methods
- ``GET /positions``                        current sized positions per instrument
- ``GET /positions/history``                position history for one instrument
- ``GET /risk``                             per-instrument vol + block decomposition
- ``GET /drawdown``                         current drawdown state + gate level
- ``POST /drawdown/release``                manual release of level-3 gate (auth)
- ``GET /equity``                           equity-curve timeseries
- ``GET /covariance``                       correlation matrix snapshot
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from api.deps import AdminDep, SessionDep
from macro_trader.db.models.portfolio import (
    CovarianceEstimate,
    DrawdownStateRow,
    EquityCurveRow,
    Position,
    VolatilityEstimate,
)
from macro_trader.db.models.system import MethodRegistryRow
from macro_trader.portfolio.drawdown.runner import (
    load_drawdown_state,
    update_drawdown_state,
)
from macro_trader.utils.dates import utcnow

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# /methods
# ----------------------------------------------------------------------
class PortfolioMethodOut(BaseModel):
    method_id: str
    component: str
    name: str
    status: str


@router.get("/methods", response_model=list[PortfolioMethodOut])
def list_portfolio_methods(session: SessionDep) -> list[PortfolioMethodOut]:
    """All registered portfolio + covariance methods."""
    rows = list(
        session.scalars(
            select(MethodRegistryRow).where(
                MethodRegistryRow.component.in_(
                    ["portfolio_construction", "covariance_estimate"]
                )
            )
        )
    )
    return [
        PortfolioMethodOut(
            method_id=r.method_id,
            component=r.component,
            name=r.name,
            status=r.status.value,
        )
        for r in rows
    ]


# ----------------------------------------------------------------------
# /positions
# ----------------------------------------------------------------------
class PositionOut(BaseModel):
    method_id: str
    instrument_id: str
    as_of: datetime
    target_weight: float
    pre_gate_weight: float | None
    expected_vol_contribution: float | None
    composite_score: float | None
    block: str | None
    gate_level: str | None
    gate_scaling_factor: float | None


@router.get("/positions", response_model=list[PositionOut])
def positions(
    session: SessionDep,
    method_id: str = Query(default="portfolio.erc.v1"),
    as_of: datetime | None = Query(default=None),
) -> list[PositionOut]:
    """Current sized positions per instrument for the chosen method."""
    target = as_of if as_of is not None else utcnow()
    latest = session.scalar(
        select(func.max(Position.as_of))
        .where(Position.method_id == method_id)
        .where(Position.as_of <= target)
    )
    if latest is None:
        return []
    rows = list(
        session.scalars(
            select(Position)
            .where(Position.method_id == method_id)
            .where(Position.as_of == latest)
        )
    )
    rows.sort(key=lambda r: -abs(float(r.target_weight)))
    return [
        PositionOut(
            method_id=r.method_id,
            instrument_id=r.instrument_id,
            as_of=r.as_of,
            target_weight=float(r.target_weight),
            pre_gate_weight=_opt_float(r.pre_gate_weight),
            expected_vol_contribution=_opt_float(r.expected_vol_contribution),
            composite_score=_opt_float(r.composite_score),
            block=r.block,
            gate_level=r.gate_level,
            gate_scaling_factor=_opt_float(r.gate_scaling_factor),
        )
        for r in rows
    ]


# ----------------------------------------------------------------------
# /positions/history
# ----------------------------------------------------------------------
class PositionHistoryPoint(BaseModel):
    as_of: datetime
    target_weight: float
    pre_gate_weight: float | None


@router.get(
    "/positions/history",
    response_model=list[PositionHistoryPoint],
)
def positions_history(
    session: SessionDep,
    instrument: str = Query(...),
    method_id: str = Query(default="portfolio.erc.v1"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> list[PositionHistoryPoint]:
    stmt = (
        select(Position)
        .where(Position.method_id == method_id)
        .where(Position.instrument_id == instrument)
        .order_by(Position.as_of)
    )
    if from_ is not None:
        stmt = stmt.where(Position.as_of >= from_)
    if to is not None:
        stmt = stmt.where(Position.as_of <= to)
    rows = list(session.scalars(stmt))
    return [
        PositionHistoryPoint(
            as_of=r.as_of,
            target_weight=float(r.target_weight),
            pre_gate_weight=_opt_float(r.pre_gate_weight),
        )
        for r in rows
    ]


# ----------------------------------------------------------------------
# /risk
# ----------------------------------------------------------------------
class RiskInstrument(BaseModel):
    instrument_id: str
    volatility: float | None
    vol_contribution: float | None
    block: str | None


class RiskOut(BaseModel):
    method_id: str
    as_of: datetime | None
    instruments: list[RiskInstrument]
    block_exposure: dict[str, float]
    portfolio_vol: float | None


@router.get("/risk", response_model=RiskOut)
def portfolio_risk(
    session: SessionDep,
    method_id: str = Query(default="portfolio.erc.v1"),
    covariance_method_id: str = Query(default="covariance.ledoit_wolf.v1"),
    as_of: datetime | None = Query(default=None),
) -> RiskOut:
    """Per-instrument vol + block decomposition for the latest
    positions snapshot."""
    target = as_of if as_of is not None else utcnow()
    pos_ts = session.scalar(
        select(func.max(Position.as_of))
        .where(Position.method_id == method_id)
        .where(Position.as_of <= target)
    )
    if pos_ts is None:
        return RiskOut(
            method_id=method_id,
            as_of=None,
            instruments=[],
            block_exposure={},
            portfolio_vol=None,
        )
    positions_rows = list(
        session.scalars(
            select(Position)
            .where(Position.method_id == method_id)
            .where(Position.as_of == pos_ts)
        )
    )
    inst_ids = [p.instrument_id for p in positions_rows]
    vol_rows = list(
        session.scalars(
            select(VolatilityEstimate)
            .where(VolatilityEstimate.method_id == covariance_method_id)
            .where(VolatilityEstimate.instrument_id.in_(inst_ids))
            .where(VolatilityEstimate.as_of <= target)
            .order_by(desc(VolatilityEstimate.as_of))
        )
    )
    latest_vol: dict[str, float] = {}
    for r in vol_rows:
        if r.instrument_id not in latest_vol:
            latest_vol[r.instrument_id] = float(r.volatility or 0.0)

    block_exposure: dict[str, float] = {}
    gross = sum(abs(float(p.target_weight)) for p in positions_rows)
    instruments_out: list[RiskInstrument] = []
    portfolio_vol: float | None = None
    for p in positions_rows:
        block = p.block or "unknown"
        block_exposure[block] = block_exposure.get(block, 0.0) + abs(float(p.target_weight))
        instruments_out.append(
            RiskInstrument(
                instrument_id=p.instrument_id,
                volatility=_opt_float(latest_vol.get(p.instrument_id)),
                vol_contribution=_opt_float(p.expected_vol_contribution),
                block=p.block,
            )
        )
    if gross > 0:
        block_exposure = {k: v / gross for k, v in block_exposure.items()}
    # Read the portfolio vol off the most-recent position metadata.
    if positions_rows:
        portfolio_vol = _opt_float(
            (positions_rows[0].position_metadata or {}).get("expected_portfolio_vol")
        )
    return RiskOut(
        method_id=method_id,
        as_of=pos_ts,
        instruments=instruments_out,
        block_exposure=block_exposure,
        portfolio_vol=portfolio_vol,
    )


# ----------------------------------------------------------------------
# /drawdown + /drawdown/release
# ----------------------------------------------------------------------
class DrawdownOut(BaseModel):
    method_id: str
    current_gate_level: str
    effective_scaling_factor: float
    level_1_triggered_at: datetime | None
    level_1_release_at: datetime | None
    level_2_triggered_at: datetime | None
    level_2_release_at: datetime | None
    level_3_triggered_at: datetime | None
    updated_at: datetime | None


@router.get("/drawdown", response_model=DrawdownOut)
def portfolio_drawdown(
    session: SessionDep,
    method_id: str = Query(default="portfolio.erc.v1"),
) -> DrawdownOut:
    row = session.scalar(
        select(DrawdownStateRow).where(DrawdownStateRow.method_id == method_id)
    )
    if row is None:
        return DrawdownOut(
            method_id=method_id,
            current_gate_level="none",
            effective_scaling_factor=1.0,
            level_1_triggered_at=None,
            level_1_release_at=None,
            level_2_triggered_at=None,
            level_2_release_at=None,
            level_3_triggered_at=None,
            updated_at=None,
        )
    return DrawdownOut(
        method_id=row.method_id,
        current_gate_level=row.current_gate_level,
        effective_scaling_factor=float(row.effective_scaling_factor),
        level_1_triggered_at=row.level_1_triggered_at,
        level_1_release_at=row.level_1_release_at,
        level_2_triggered_at=row.level_2_triggered_at,
        level_2_release_at=row.level_2_release_at,
        level_3_triggered_at=row.level_3_triggered_at,
        updated_at=row.updated_at,
    )


class DrawdownReleaseOut(BaseModel):
    method_id: str
    released: bool
    new_state: DrawdownOut


@router.post(
    "/drawdown/release",
    response_model=DrawdownReleaseOut,
    status_code=status.HTTP_200_OK,
)
def portfolio_drawdown_release(
    session: SessionDep,
    _admin: AdminDep,
    method_id: str = Query(default="portfolio.erc.v1"),
) -> DrawdownReleaseOut:
    """Manual release of the level-3 gate. Requires admin auth.

    Re-runs ``update_drawdown_state`` with the manual flag set so
    the gate machinery can audit-log the release rather than
    short-circuit it.
    """
    prior = load_drawdown_state(session, method_id=method_id)
    if not prior.level_3.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="level_3 gate is not active",
        )
    new_state = update_drawdown_state(
        session,
        method_id=method_id,
        level_3_manual_release_requested=True,
    )
    session.commit()
    return DrawdownReleaseOut(
        method_id=method_id,
        released=not new_state.level_3.is_active,
        new_state=DrawdownOut(
            method_id=method_id,
            current_gate_level=new_state.current_level,
            effective_scaling_factor=new_state.effective_scaling_factor,
            level_1_triggered_at=new_state.level_1.triggered_at,
            level_1_release_at=new_state.level_1.release_at,
            level_2_triggered_at=new_state.level_2.triggered_at,
            level_2_release_at=new_state.level_2.release_at,
            level_3_triggered_at=new_state.level_3.triggered_at,
            updated_at=new_state.as_of,
        ),
    )


# ----------------------------------------------------------------------
# /equity
# ----------------------------------------------------------------------
class EquityPoint(BaseModel):
    as_of: datetime
    nav: float
    daily_return: float | None
    cumulative_return: float | None
    peak_nav: float | None
    drawdown_from_peak: float | None


@router.get("/equity", response_model=list[EquityPoint])
def portfolio_equity(
    session: SessionDep,
    method_id: str = Query(default="portfolio.erc.v1"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> list[EquityPoint]:
    stmt = (
        select(EquityCurveRow)
        .where(EquityCurveRow.method_id == method_id)
        .order_by(EquityCurveRow.as_of)
    )
    if from_ is not None:
        stmt = stmt.where(EquityCurveRow.as_of >= from_)
    if to is not None:
        stmt = stmt.where(EquityCurveRow.as_of <= to)
    rows = list(session.scalars(stmt))
    return [
        EquityPoint(
            as_of=r.as_of,
            nav=float(r.nav),
            daily_return=_opt_float(r.daily_return),
            cumulative_return=_opt_float(r.cumulative_return),
            peak_nav=_opt_float(r.peak_nav),
            drawdown_from_peak=_opt_float(r.drawdown_from_peak),
        )
        for r in rows
    ]


# ----------------------------------------------------------------------
# /covariance
# ----------------------------------------------------------------------
class CovariancePoint(BaseModel):
    instrument_a: str
    instrument_b: str
    covariance: float | None
    correlation: float | None


class CovarianceMatrixOut(BaseModel):
    method_id: str
    as_of: datetime | None
    points: list[CovariancePoint]


@router.get("/covariance", response_model=CovarianceMatrixOut)
def portfolio_covariance(
    session: SessionDep,
    method_id: str = Query(default="covariance.ledoit_wolf.v1"),
    as_of: datetime | None = Query(default=None),
) -> CovarianceMatrixOut:
    target = as_of if as_of is not None else utcnow()
    latest = session.scalar(
        select(func.max(CovarianceEstimate.as_of))
        .where(CovarianceEstimate.method_id == method_id)
        .where(CovarianceEstimate.as_of <= target)
    )
    if latest is None:
        return CovarianceMatrixOut(
            method_id=method_id, as_of=None, points=[]
        )
    rows = list(
        session.scalars(
            select(CovarianceEstimate)
            .where(CovarianceEstimate.method_id == method_id)
            .where(CovarianceEstimate.as_of == latest)
        )
    )
    return CovarianceMatrixOut(
        method_id=method_id,
        as_of=latest,
        points=[
            CovariancePoint(
                instrument_a=r.instrument_a,
                instrument_b=r.instrument_b,
                covariance=_opt_float(r.covariance),
                correlation=_opt_float(r.correlation),
            )
            for r in rows
        ],
    )
