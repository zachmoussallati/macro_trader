"""Signal API routes."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select

from api.deps import SessionDep
from macro_trader.db.models.positioning import COTWeekly
from macro_trader.db.models.signals import SignalValue
from macro_trader.db.models.system import (
    MethodComparisonRow,
    MethodRegistryRow,
)
from macro_trader.signals.designated import resolve_id
from macro_trader.utils.dates import utcnow

router = APIRouter(prefix="/signals", tags=["signals"])


# ----------------------------------------------------------------------
# Pydantic shapes
# ----------------------------------------------------------------------
class SignalMetaOut(BaseModel):
    signal_id: str
    component: str
    name: str
    version: str
    status: str

    @classmethod
    def from_row(cls, row: MethodRegistryRow) -> SignalMetaOut:
        return cls(
            signal_id=row.method_id,
            component=row.component,
            name=row.name,
            version=row.version,
            status=str(row.status),
        )


class SignalValueOut(BaseModel):
    signal_id: str
    instrument_id: str
    value_ts: datetime
    observation_ts: datetime
    raw_value: float | None
    zscore: float | None
    rank: float | None
    confidence: float | None
    rolling_sharpe_252: float | None
    metadata: dict[str, Any]

    @classmethod
    def from_row(cls, row: SignalValue) -> SignalValueOut:
        return cls(
            signal_id=row.signal_id,
            instrument_id=row.instrument_id,
            value_ts=row.value_ts,
            observation_ts=row.observation_ts,
            raw_value=_opt_float(row.raw_value),
            zscore=_opt_float(row.zscore),
            rank=_opt_float(row.rank),
            confidence=_opt_float(row.confidence),
            rolling_sharpe_252=_opt_float(row.rolling_sharpe_252),
            metadata=dict(row.signal_metadata or {}),
        )


class ComparisonOut(BaseModel):
    comparison_id: str
    component: str
    method_a_id: str
    method_b_id: str
    period_start: datetime
    period_end: datetime
    metrics: dict[str, Any]
    agreement: dict[str, Any]
    stability: dict[str, Any]
    notes: str
    created_at: datetime


class HeatmapCellOut(BaseModel):
    instrument_id: str
    component: str
    signal_id: str
    raw_value: float | None
    zscore: float | None
    rank: float | None
    confidence: float | None
    value_ts: datetime


# Components whose latest values populate the dashboard heatmap. The
# *id* of the designated method per component is resolved at request
# time via :func:`macro_trader.signals.designated.resolve_id`
# (config override -> registry PRODUCTION -> BASELINE -> first-registered).
# Adding a component here makes the heatmap query it; the resolver
# handles the "which method's values?" question.
COMPONENTS_FOR_HEATMAP: tuple[str, ...] = (
    "trend_signal",
    "carry_signal",
    "value_signal",
    "positioning_signal",
    "dislocation_signal",
)


def _designated_signal_ids() -> dict[str, str]:
    """Resolve the designated method id per component, skipping any
    component that isn't yet registered."""
    out: dict[str, str] = {}
    for component in COMPONENTS_FOR_HEATMAP:
        sid = resolve_id(component)
        if sid is not None:
            out[component] = sid
    return out


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
@router.get("", response_model=list[SignalMetaOut])
def list_signals(session: SessionDep) -> list[SignalMetaOut]:
    rows = list(
        session.scalars(
            select(MethodRegistryRow)
            .where(MethodRegistryRow.component.in_(list(COMPONENTS_FOR_HEATMAP)))
            .order_by(MethodRegistryRow.component, MethodRegistryRow.method_id)
        )
    )
    return [SignalMetaOut.from_row(r) for r in rows]


@router.get("/heatmap", response_model=list[HeatmapCellOut])
def heatmap(
    session: SessionDep,
    as_of: datetime | None = Query(default=None),
) -> list[HeatmapCellOut]:
    """Latest cell per (component, instrument). Designated method per family."""
    target = as_of if as_of is not None else utcnow()
    out: list[HeatmapCellOut] = []
    for component, signal_id in _designated_signal_ids().items():
        # For each instrument, take the most recent value_ts where
        # observation_ts <= as_of.
        subq = (
            select(
                SignalValue.instrument_id.label("inst"),
                func.max(SignalValue.value_ts).label("max_value_ts"),
            )
            .where(SignalValue.signal_id == signal_id)
            .where(SignalValue.observation_ts <= target)
            .group_by(SignalValue.instrument_id)
            .subquery()
        )
        stmt = (
            select(SignalValue)
            .join(
                subq,
                and_(
                    SignalValue.instrument_id == subq.c.inst,
                    SignalValue.value_ts == subq.c.max_value_ts,
                    SignalValue.signal_id == signal_id,
                ),
            )
            .where(SignalValue.observation_ts <= target)
        )
        rows = list(session.scalars(stmt))
        for r in rows:
            out.append(
                HeatmapCellOut(
                    instrument_id=r.instrument_id,
                    component=component,
                    signal_id=signal_id,
                    raw_value=_opt_float(r.raw_value),
                    zscore=_opt_float(r.zscore),
                    rank=_opt_float(r.rank),
                    confidence=_opt_float(r.confidence),
                    value_ts=r.value_ts,
                )
            )
    return out


@router.get("/values", response_model=list[SignalValueOut])
def latest_values_cross_component(
    session: SessionDep,
    instrument: str | None = Query(default=None),
    as_of: datetime | None = Query(default=None),
) -> list[SignalValueOut]:
    """Latest value per (signal_id, instrument_id) visible at ``as_of``.

    With no filter you get one row per registered signal x instrument; with
    ``instrument`` set you get one row per signal for that instrument.
    """
    target = as_of if as_of is not None else utcnow()
    subq_base = select(
        SignalValue.signal_id.label("sig"),
        SignalValue.instrument_id.label("inst"),
        func.max(SignalValue.value_ts).label("max_value_ts"),
    ).where(SignalValue.observation_ts <= target)
    if instrument:
        subq_base = subq_base.where(SignalValue.instrument_id == instrument)
    subq = subq_base.group_by(SignalValue.signal_id, SignalValue.instrument_id).subquery()

    stmt = (
        select(SignalValue)
        .join(
            subq,
            and_(
                SignalValue.signal_id == subq.c.sig,
                SignalValue.instrument_id == subq.c.inst,
                SignalValue.value_ts == subq.c.max_value_ts,
            ),
        )
        .where(SignalValue.observation_ts <= target)
        .order_by(SignalValue.signal_id, SignalValue.instrument_id)
    )
    rows = list(session.scalars(stmt))
    return [SignalValueOut.from_row(r) for r in rows]


@router.get("/comparisons", response_model=list[ComparisonOut])
def list_comparisons(
    session: SessionDep,
    component: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
) -> list[ComparisonOut]:
    stmt = select(MethodComparisonRow).order_by(MethodComparisonRow.created_at.desc()).limit(limit)
    if component is not None:
        stmt = stmt.where(MethodComparisonRow.component == component)
    rows = list(session.scalars(stmt))
    return [ComparisonOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/{signal_id}", response_model=SignalMetaOut)
def get_signal(signal_id: str, session: SessionDep) -> SignalMetaOut:
    row = session.get(MethodRegistryRow, signal_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="signal not found")
    return SignalMetaOut.from_row(row)


@router.get("/{signal_id}/values", response_model=list[SignalValueOut])
def list_signal_values(
    signal_id: str,
    session: SessionDep,
    instrument: str | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    as_of: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[SignalValueOut]:
    target = as_of if as_of is not None else utcnow()
    stmt = (
        select(SignalValue)
        .where(SignalValue.signal_id == signal_id)
        .where(SignalValue.observation_ts <= target)
    )
    if instrument:
        stmt = stmt.where(SignalValue.instrument_id == instrument)
    if from_ is not None:
        stmt = stmt.where(SignalValue.value_ts >= from_)
    if to is not None:
        stmt = stmt.where(SignalValue.value_ts <= to)
    stmt = stmt.order_by(SignalValue.value_ts.desc()).limit(limit)
    rows = list(session.scalars(stmt))
    return [SignalValueOut.from_row(r) for r in rows]


class PositioningCotPointOut(BaseModel):
    """One COT row exposed via ``/signals/positioning/cot``."""

    report_ts: datetime
    publication_ts: datetime
    report_type: str
    open_interest: float | None
    managed_money_long: float | None
    managed_money_short: float | None
    managed_money_net: float | None
    producer_long: float | None
    producer_short: float | None
    producer_net: float | None
    swap_long: float | None
    swap_short: float | None
    nonreportable_long: float | None
    nonreportable_short: float | None


def _net(long: float | None, short: float | None) -> float | None:
    if long is None or short is None:
        return None
    return float(long) - float(short)


@router.get("/positioning/cot", response_model=list[PositioningCotPointOut])
def positioning_cot(
    session: SessionDep,
    instrument: str = Query(..., min_length=1, max_length=32),
    report_type: str = Query(default="disaggregated"),
    as_of: datetime | None = Query(default=None),
    lookback_weeks: int = Query(default=156, ge=1, le=520),
) -> list[PositioningCotPointOut]:
    """Per-instrument COT breakdown over time, latest-vintage as of ``as_of``.

    The positioning signal pages on the dashboard use this for the
    stacked-area chart of managed money / commercials / nonreportable
    long-short over time. ``report_type`` selects which CFTC report
    breakdown to read (``disaggregated`` / ``legacy`` / ``financial_tff``).
    """
    if report_type not in ("disaggregated", "legacy", "financial_tff"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unsupported report_type {report_type!r}; expected one of "
                "disaggregated / legacy / financial_tff"
            ),
        )
    target = as_of if as_of is not None else utcnow()
    earliest = target - timedelta(weeks=lookback_weeks)
    rows = list(
        session.scalars(
            select(COTWeekly)
            .where(COTWeekly.instrument_id == instrument)
            .where(COTWeekly.report_type == report_type)
            .where(COTWeekly.publication_ts <= target)
            .where(COTWeekly.report_ts >= earliest)
            .order_by(COTWeekly.report_ts.asc())
        )
    )
    return [
        PositioningCotPointOut(
            report_ts=r.report_ts,
            publication_ts=r.publication_ts,
            report_type=r.report_type,
            open_interest=_opt_float(r.open_interest),
            managed_money_long=_opt_float(r.managed_money_long),
            managed_money_short=_opt_float(r.managed_money_short),
            managed_money_net=_net(r.managed_money_long, r.managed_money_short),
            producer_long=_opt_float(r.producer_long),
            producer_short=_opt_float(r.producer_short),
            producer_net=_net(r.producer_long, r.producer_short),
            swap_long=_opt_float(r.swap_long),
            swap_short=_opt_float(r.swap_short),
            nonreportable_long=_opt_float(r.nonreportable_long),
            nonreportable_short=_opt_float(r.nonreportable_short),
        )
        for r in rows
    ]


class DislocationFactorOut(BaseModel):
    """One per-instrument dislocation snapshot for ``/signals/dislocation/factors``."""

    instrument_id: str
    method_id: str
    value_ts: datetime
    raw_value: float | None
    zscore: float | None
    rank: float | None
    confidence: float | None
    explained_variance: float | None


@router.get(
    "/dislocation/factors", response_model=list[DislocationFactorOut]
)
def dislocation_factors(
    session: SessionDep,
    method_id: str = Query(
        default="dislocation.pca.v1",
        description="Which dislocation method's snapshot to read.",
    ),
    as_of: datetime | None = Query(default=None),
) -> list[DislocationFactorOut]:
    """Latest per-instrument dislocation snapshot for the requested method.

    Returns one row per instrument with the most recent ``signal_value``
    visible at ``as_of`` plus the method's stored
    ``metadata.explained_variance``. Stage 4A's PCA + DFM are re-fit
    on every run so the explained-variance field reflects the latest
    fit; a future weekly-refit asset will persist fitted state into the
    registry blob (see notes/stage_4a/tradeoffs.md)."""
    if method_id not in ("dislocation.pca.v1", "dislocation.dfm.v1"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unknown dislocation method_id {method_id!r}; "
                "expected dislocation.pca.v1 or dislocation.dfm.v1"
            ),
        )
    target = as_of if as_of is not None else utcnow()
    subq = (
        select(
            SignalValue.instrument_id.label("inst"),
            func.max(SignalValue.value_ts).label("max_value_ts"),
        )
        .where(SignalValue.signal_id == method_id)
        .where(SignalValue.observation_ts <= target)
        .group_by(SignalValue.instrument_id)
        .subquery()
    )
    stmt = (
        select(SignalValue)
        .join(
            subq,
            and_(
                SignalValue.instrument_id == subq.c.inst,
                SignalValue.value_ts == subq.c.max_value_ts,
                SignalValue.signal_id == method_id,
            ),
        )
        .where(SignalValue.observation_ts <= target)
    )
    rows = list(session.scalars(stmt))
    out: list[DislocationFactorOut] = []
    for r in rows:
        explained = None
        meta = r.signal_metadata or {}
        if isinstance(meta, dict):
            explained = _opt_float(meta.get("explained_variance"))
        out.append(
            DislocationFactorOut(
                instrument_id=r.instrument_id,
                method_id=method_id,
                value_ts=r.value_ts,
                raw_value=_opt_float(r.raw_value),
                zscore=_opt_float(r.zscore),
                rank=_opt_float(r.rank),
                confidence=_opt_float(r.confidence),
                explained_variance=explained,
            )
        )
    return out


class DecayPointOut(BaseModel):
    value_ts: datetime
    rolling_sharpe_252: float | None


@router.get("/{signal_id}/decay", response_model=list[DecayPointOut])
def signal_decay(
    signal_id: str,
    session: SessionDep,
    instrument: str | None = Query(default=None),
    lookback_days: int = Query(default=365, ge=1, le=3650),
) -> list[DecayPointOut]:
    """Rolling 252-day Sharpe of the signal over time.

    With no instrument filter we average across instruments per value_ts.
    """
    cutoff = utcnow() - timedelta(days=lookback_days)
    stmt = (
        select(SignalValue.value_ts, SignalValue.rolling_sharpe_252)
        .where(SignalValue.signal_id == signal_id)
        .where(SignalValue.value_ts >= cutoff)
        .order_by(SignalValue.value_ts)
    )
    if instrument:
        stmt = stmt.where(SignalValue.instrument_id == instrument)
    rows = list(session.execute(stmt).all())
    if not rows:
        return []
    if instrument:
        return [DecayPointOut(value_ts=v, rolling_sharpe_252=_opt_float(s)) for v, s in rows]
    # No instrument filter — aggregate by value_ts.
    grouped: dict[datetime, list[float]] = {}
    for v, s in rows:
        if s is None:
            continue
        grouped.setdefault(v, []).append(float(s))
    return [
        DecayPointOut(
            value_ts=v,
            rolling_sharpe_252=(sum(vals) / len(vals)) if vals else None,
        )
        for v, vals in sorted(grouped.items())
    ]
