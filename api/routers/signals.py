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
    "factor_exposure_signal",
    "catalyst_signal",
    "vol_surface_signal",
    "nowcasting_signal",
    "alt_data_signal",
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


class UpcomingCatalystOut(BaseModel):
    """One upcoming-catalyst row for ``/signals/catalyst/events``."""

    event_ts: datetime
    subject: str
    kind: str
    importance: str
    affected_instruments: list[str]


@router.get("/catalyst/events", response_model=list[UpcomingCatalystOut])
def catalyst_events(
    session: SessionDep,
    as_of: datetime | None = Query(default=None),
    days_ahead: int = Query(default=10, ge=1, le=60),
) -> list[UpcomingCatalystOut]:
    """Upcoming events in the next ``days_ahead`` days that the catalyst
    signal will fold into per-instrument forward scores."""
    from macro_trader.calendar.api import events_in_window

    target = as_of if as_of is not None else utcnow()
    rows = events_in_window(
        session,
        start=target,
        end=target + timedelta(days=days_ahead),
        importance=["medium", "high"],
        kinds=["data_release", "central_bank", "supply_event"],
    )
    return [
        UpcomingCatalystOut(
            event_ts=r.event_ts,
            subject=r.subject,
            kind=r.kind,
            importance=r.importance,
            affected_instruments=list(r.affected_instruments or []),
        )
        for r in rows
    ]


# ----------------------------------------------------------------------
# Stage 4C additions
# ----------------------------------------------------------------------


class CatalystHistoricalOut(BaseModel):
    """One historical event-by-event observation for the catalyst page."""

    event_ts: datetime
    subject: str
    log_return: float
    abs_return: float


@router.get("/catalyst/historical", response_model=list[CatalystHistoricalOut])
def catalyst_historical(
    session: SessionDep,
    instrument_id: str = Query(..., min_length=1, max_length=32),
    event_subject: str | None = Query(default=None),
    lookback_years: int = Query(default=5, ge=1, le=20),
) -> list[CatalystHistoricalOut]:
    """Per-event historical returns around catalyst events for an
    (instrument, event_subject) pair (or every subject when omitted).

    Drives the catalyst page's "historical sensitivity" detail table:
    the user clicks an instrument in the catalyst pressure bar chart
    and sees the event-by-event returns that drive its forward score.
    """
    from macro_trader.signals.catalyst.events import (
        DEFAULT_EVENT_KINDS,
        DEFAULT_IMPORTANCE,
        historical_event_returns,
    )

    historicals = historical_event_returns(
        session,
        instrument_ids=[instrument_id],
        as_of=utcnow(),
        lookback_years=lookback_years,
        kinds=DEFAULT_EVENT_KINDS,
        importance=DEFAULT_IMPORTANCE,
    )
    out = [
        CatalystHistoricalOut(
            event_ts=h.event_ts,
            subject=h.subject,
            log_return=float(h.log_return),
            abs_return=abs(float(h.log_return)),
        )
        for h in historicals
        if event_subject is None or h.subject == event_subject
    ]
    out.sort(key=lambda r: r.event_ts)
    return out


class CatalystPressureOut(BaseModel):
    """Per-instrument forward catalyst pressure score."""

    instrument_id: str
    pressure_score: float | None
    n_subjects: int
    raw_value: float | None
    rank: float | None
    confidence: float | None
    value_ts: datetime | None


@router.get("/catalyst/pressure", response_model=list[CatalystPressureOut])
def catalyst_pressure(
    session: SessionDep,
    as_of: datetime | None = Query(default=None),
    method_id: str = Query(default="catalyst.event_study.v1"),
) -> list[CatalystPressureOut]:
    """Per-instrument forward catalyst pressure (latest signal_value
    snapshot for the chosen catalyst method).

    Drives the catalyst page's bar chart sorted by pressure magnitude.
    """
    if method_id not in ("catalyst.event_study.v1", "catalyst.causal.v1"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown catalyst method_id {method_id!r}",
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
    out: list[CatalystPressureOut] = []
    for r in rows:
        meta = r.signal_metadata or {}
        n_subjects = 0
        if isinstance(meta, dict):
            n_subjects = int(meta.get("n_subjects", 0) or 0)
        out.append(
            CatalystPressureOut(
                instrument_id=r.instrument_id,
                pressure_score=_opt_float(r.zscore),  # pre-tanh forward score
                n_subjects=n_subjects,
                raw_value=_opt_float(r.raw_value),
                rank=_opt_float(r.rank),
                confidence=_opt_float(r.confidence),
                value_ts=r.value_ts,
            )
        )
    out.sort(
        key=lambda x: abs(x.pressure_score) if x.pressure_score is not None else 0.0,
        reverse=True,
    )
    return out


class PositioningBreakdownPoint(BaseModel):
    """One time-step in the positioning breakdown chart."""

    report_ts: datetime
    publication_ts: datetime
    long: float | None
    short: float | None
    net: float | None
    open_interest: float | None
    raw_value: float | None
    zscore: float | None
    confidence: float | None


@router.get(
    "/positioning/breakdown", response_model=list[PositioningBreakdownPoint]
)
def positioning_breakdown(
    session: SessionDep,
    instrument_id: str = Query(..., min_length=1, max_length=32),
    method_id: str = Query(default="positioning.cot_zscore.v1"),
    as_of: datetime | None = Query(default=None),
    lookback_weeks: int = Query(default=156, ge=1, le=520),
) -> list[PositioningBreakdownPoint]:
    """Time series of COT positioning breakdown joined with the
    computed signal values for the chosen method.

    For ``positioning.cot_zscore.v1`` we read managed-money long /
    short / net from the disaggregated report; for
    ``positioning.cot_commercial.v1`` we read producer (commercial)
    long / short / net from the legacy report. Signal values join in
    on the matching ``value_ts``.
    """
    if method_id == "positioning.cot_zscore.v1":
        report_type = "disaggregated"
        long_attr = "managed_money_long"
        short_attr = "managed_money_short"
    elif method_id == "positioning.cot_commercial.v1":
        report_type = "legacy"
        long_attr = "producer_long"
        short_attr = "producer_short"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown positioning method_id {method_id!r}",
        )

    target = as_of if as_of is not None else utcnow()
    earliest = target - timedelta(weeks=lookback_weeks)
    cot_rows = list(
        session.scalars(
            select(COTWeekly)
            .where(COTWeekly.instrument_id == instrument_id)
            .where(COTWeekly.report_type == report_type)
            .where(COTWeekly.publication_ts <= target)
            .where(COTWeekly.report_ts >= earliest)
            .order_by(COTWeekly.report_ts.asc())
        )
    )
    signal_rows = list(
        session.scalars(
            select(SignalValue)
            .where(SignalValue.signal_id == method_id)
            .where(SignalValue.instrument_id == instrument_id)
            .where(SignalValue.observation_ts <= target)
            .order_by(SignalValue.value_ts.asc())
        )
    )
    sig_by_ts = {sv.value_ts: sv for sv in signal_rows}

    out: list[PositioningBreakdownPoint] = []
    for r in cot_rows:
        long_v = _opt_float(getattr(r, long_attr))
        short_v = _opt_float(getattr(r, short_attr))
        net_v = _net(getattr(r, long_attr), getattr(r, short_attr))
        sig = sig_by_ts.get(r.report_ts)
        out.append(
            PositioningBreakdownPoint(
                report_ts=r.report_ts,
                publication_ts=r.publication_ts,
                long=long_v,
                short=short_v,
                net=net_v,
                open_interest=_opt_float(r.open_interest),
                raw_value=_opt_float(sig.raw_value) if sig else None,
                zscore=_opt_float(sig.zscore) if sig else None,
                confidence=_opt_float(sig.confidence) if sig else None,
            )
        )
    return out


class DislocationExplainedVariancePoint(BaseModel):
    """One observation of explained_variance over time."""

    value_ts: datetime
    explained_variance: float | None


@router.get(
    "/dislocation/explained_variance",
    response_model=list[DislocationExplainedVariancePoint],
)
def dislocation_explained_variance(
    session: SessionDep,
    method_id: str = Query(default="dislocation.pca.v1"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> list[DislocationExplainedVariancePoint]:
    """Time series of ``signal_values.metadata.explained_variance`` for
    the chosen dislocation method. PCA's value steps weekly (by Stage
    4B refit cadence); DFM's evolves smoothly.

    Aggregates per-``value_ts`` by averaging across instruments — every
    instrument in the same fit shares the same explained_variance, so
    the average is just a deduplication.
    """
    if method_id not in ("dislocation.pca.v1", "dislocation.dfm.v1"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown dislocation method_id {method_id!r}",
        )
    stmt = (
        select(SignalValue)
        .where(SignalValue.signal_id == method_id)
        .order_by(SignalValue.value_ts.asc())
    )
    if from_ is not None:
        stmt = stmt.where(SignalValue.value_ts >= from_)
    if to is not None:
        stmt = stmt.where(SignalValue.value_ts <= to)
    rows = list(session.scalars(stmt))

    by_ts: dict[datetime, list[float]] = {}
    for r in rows:
        meta = r.signal_metadata or {}
        ev = None
        if isinstance(meta, dict):
            ev = _opt_float(meta.get("explained_variance"))
        if ev is not None:
            by_ts.setdefault(r.value_ts, []).append(ev)

    out: list[DislocationExplainedVariancePoint] = []
    for ts in sorted(by_ts):
        values = by_ts[ts]
        avg = sum(values) / len(values) if values else None
        out.append(
            DislocationExplainedVariancePoint(value_ts=ts, explained_variance=avg)
        )
    return out


class FactorContributionRow(BaseModel):
    """One factor's contribution to a per-instrument signal score."""

    factor_name: str
    loading: float
    zscore: float | None
    contribution: float


class FactorContributionsOut(BaseModel):
    instrument_id: str
    method_id: str
    raw_value: float | None
    sum_contributions: float
    contributions: list[FactorContributionRow]


@router.get(
    "/factor_exposure/contributions", response_model=FactorContributionsOut
)
def factor_exposure_contributions(
    session: SessionDep,
    instrument_id: str = Query(..., min_length=1, max_length=32),
    method_id: str = Query(default="factor_exposure.ols.v1"),
    as_of: datetime | None = Query(default=None),
) -> FactorContributionsOut:
    """Per-factor contribution to the current signal score.

    For OLS: loading = beta, contribution = -beta * z_today (matches
    the long-bias convention's negation). For RF / CF, loading is
    feature-importance / CATE; contribution uses the same sign rule.
    Sum of contributions matches the stored ``zscore`` (pre-tanh
    raw score) modulo numerical rounding.
    """
    if method_id not in (
        "factor_exposure.ols.v1",
        "factor_exposure.rf.v1",
        "factor_exposure.causal_forest.v1",
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown factor exposure method_id {method_id!r}",
        )

    target = as_of if as_of is not None else utcnow()
    sv = (
        session.scalars(
            select(SignalValue)
            .where(SignalValue.signal_id == method_id)
            .where(SignalValue.instrument_id == instrument_id)
            .where(SignalValue.observation_ts <= target)
            .order_by(SignalValue.value_ts.desc())
            .limit(1)
        )
    ).first()
    if sv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no {method_id} signal value yet for instrument "
                f"{instrument_id!r}"
            ),
        )

    meta = sv.signal_metadata or {}
    loadings: dict[str, float] = {}
    zscores: dict[str, float] = {}
    if isinstance(meta, dict):
        raw_loadings = meta.get("factor_loadings", {}) or {}
        raw_zs = meta.get("factor_zscores", {}) or {}
        if isinstance(raw_loadings, dict):
            loadings = {
                k: float(v)
                for k, v in raw_loadings.items()
                if isinstance(v, int | float)
            }
        if isinstance(raw_zs, dict):
            zscores = {
                k: float(v)
                for k, v in raw_zs.items()
                if isinstance(v, int | float)
            }

    contributions: list[FactorContributionRow] = []
    for factor_name in sorted(set(loadings) | set(zscores)):
        loading = loadings.get(factor_name, 0.0)
        z = zscores.get(factor_name)
        contribution = -loading * (z if z is not None else 0.0)
        contributions.append(
            FactorContributionRow(
                factor_name=factor_name,
                loading=loading,
                zscore=z,
                contribution=contribution,
            )
        )
    return FactorContributionsOut(
        instrument_id=instrument_id,
        method_id=method_id,
        raw_value=_opt_float(sv.raw_value),
        sum_contributions=sum(c.contribution for c in contributions),
        contributions=contributions,
    )


class FactorZScoreOut(BaseModel):
    """Latest factor z-scores for the heat-of-the-market view."""

    factor_name: str
    zscore: float | None
    fred_series: str


@router.get("/factor_exposure/factors", response_model=list[FactorZScoreOut])
def factor_exposure_factors(
    session: SessionDep,
    as_of: datetime | None = Query(default=None),
) -> list[FactorZScoreOut]:
    """Latest macro factor z-scores (growth / inflation / liquidity /
    usd / oil / risk_on). Drives the dashboard's factor heatmap header
    and lets users see "what's the macro picture today?"."""
    from macro_trader.signals.factor_exposure.factors import (
        DEFAULT_FACTORS,
        latest_factor_zscores,
    )

    target = as_of if as_of is not None else utcnow()
    z = latest_factor_zscores(session, as_of=target)
    out: list[FactorZScoreOut] = []
    for spec in DEFAULT_FACTORS:
        out.append(
            FactorZScoreOut(
                factor_name=spec.name,
                zscore=_opt_float(z.get(spec.name)),
                fred_series=spec.fred_series,
            )
        )
    return out


class FactorExposureLoadingOut(BaseModel):
    """Per-instrument factor loading row for the factor heatmap."""

    instrument_id: str
    method_id: str
    factor_loadings: dict[str, float]
    raw_value: float | None
    rank: float | None
    confidence: float | None
    value_ts: datetime


@router.get(
    "/factor_exposure/loadings", response_model=list[FactorExposureLoadingOut]
)
def factor_exposure_loadings(
    session: SessionDep,
    method_id: str = Query(default="factor_exposure.ols.v1"),
    as_of: datetime | None = Query(default=None),
) -> list[FactorExposureLoadingOut]:
    """Per-instrument factor loadings + signal snapshot for the chosen
    factor exposure method. Reads from ``signal_values.metadata.factor_loadings``."""
    valid = {
        "factor_exposure.ols.v1",
        "factor_exposure.rf.v1",
        "factor_exposure.causal_forest.v1",
    }
    if method_id not in valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown factor exposure method_id {method_id!r}",
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
    out: list[FactorExposureLoadingOut] = []
    for r in rows:
        meta = r.signal_metadata or {}
        loadings = (
            meta.get("factor_loadings", {})
            if isinstance(meta, dict)
            else {}
        )
        out.append(
            FactorExposureLoadingOut(
                instrument_id=r.instrument_id,
                method_id=method_id,
                factor_loadings={
                    k: float(v)
                    for k, v in loadings.items()
                    if isinstance(v, int | float)
                },
                raw_value=_opt_float(r.raw_value),
                rank=_opt_float(r.rank),
                confidence=_opt_float(r.confidence),
                value_ts=r.value_ts,
            )
        )
    return out


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


# ----------------------------------------------------------------------
# Stage 5 — vol surface / nowcasting / alt-data endpoints
# ----------------------------------------------------------------------


class VolSurfaceSlicePoint(BaseModel):
    """One per-strike point inside a single expiry slice."""

    strike: float
    moneyness: float | None
    implied_vol: float | None
    option_type: str
    delta: float | None
    open_interest: int | None


class VolSurfaceSlice(BaseModel):
    """One expiry slice in the surface inspector."""

    expiry_ts: datetime
    dte: int
    n_strikes: int
    atm_iv: float | None
    points: list[VolSurfaceSlicePoint]


class VolSurfaceSlicesOut(BaseModel):
    instrument_id: str
    snapshot_ts: datetime
    underlying_price: float | None
    slices: list[VolSurfaceSlice]


@router.get("/vol_surface/slices", response_model=VolSurfaceSlicesOut)
def vol_surface_slices(
    session: SessionDep,
    instrument: str = Query(..., min_length=1, max_length=32),
    as_of: datetime | None = Query(default=None),
) -> VolSurfaceSlicesOut:
    """Per-expiry slices of the latest options chain for an
    instrument. Drives the vol_surface page's per-slice IV plots.

    Reads from ``market_data.options_chains`` directly. ATM IV per
    slice is the IV at the strike closest to spot.
    """
    from sqlalchemy import desc

    from macro_trader.db.models.market_data import OptionsChain

    target = as_of if as_of is not None else utcnow()
    latest_ts = session.scalar(
        select(func.max(OptionsChain.snapshot_ts))
        .where(OptionsChain.instrument_id == instrument)
        .where(OptionsChain.snapshot_ts <= target)
    )
    if latest_ts is None:
        return VolSurfaceSlicesOut(
            instrument_id=instrument,
            snapshot_ts=target,
            underlying_price=None,
            slices=[],
        )
    rows = list(
        session.scalars(
            select(OptionsChain)
            .where(OptionsChain.instrument_id == instrument)
            .where(OptionsChain.snapshot_ts == latest_ts)
            .order_by(OptionsChain.expiry_ts.asc(), desc(OptionsChain.strike))
        )
    )
    if not rows:
        return VolSurfaceSlicesOut(
            instrument_id=instrument,
            snapshot_ts=latest_ts,
            underlying_price=None,
            slices=[],
        )
    spot = next((r.underlying_price for r in rows if r.underlying_price), None)
    spot_f = float(spot) if spot is not None else None

    by_expiry: dict[datetime, list[OptionsChain]] = {}
    for r in rows:
        by_expiry.setdefault(r.expiry_ts, []).append(r)

    slices: list[VolSurfaceSlice] = []
    for expiry, slice_rows in sorted(by_expiry.items(), key=lambda kv: kv[0]):
        dte = max(int((expiry - latest_ts).total_seconds() / 86400.0), 0)
        atm_iv: float | None = None
        if spot_f is not None and spot_f > 0:
            call_rows = [
                r for r in slice_rows if r.option_type == "call" and r.implied_vol
            ]
            if call_rows:
                closest = min(
                    call_rows, key=lambda r: abs(float(r.strike) - spot_f)
                )
                atm_iv = float(closest.implied_vol) if closest.implied_vol else None
        slices.append(
            VolSurfaceSlice(
                expiry_ts=expiry,
                dte=dte,
                n_strikes=len(slice_rows),
                atm_iv=atm_iv,
                points=[
                    VolSurfaceSlicePoint(
                        strike=float(r.strike),
                        moneyness=(float(r.strike) / spot_f - 1.0)
                        if spot_f and spot_f > 0
                        else None,
                        implied_vol=_opt_float(r.implied_vol),
                        option_type=r.option_type,
                        delta=_opt_float(r.delta),
                        open_interest=int(r.open_interest)
                        if r.open_interest is not None
                        else None,
                    )
                    for r in slice_rows
                ],
            )
        )

    return VolSurfaceSlicesOut(
        instrument_id=instrument,
        snapshot_ts=latest_ts,
        underlying_price=spot_f,
        slices=slices,
    )


class VolSurfaceTermStructurePoint(BaseModel):
    expiry_ts: datetime
    dte: int
    atm_iv: float | None


@router.get(
    "/vol_surface/term_structure",
    response_model=list[VolSurfaceTermStructurePoint],
)
def vol_surface_term_structure(
    session: SessionDep,
    instrument: str = Query(..., min_length=1, max_length=32),
    as_of: datetime | None = Query(default=None),
) -> list[VolSurfaceTermStructurePoint]:
    """Term-structure view: ATM IV per expiry for the latest chain
    snapshot. The vol_surface page's middle chart."""
    full = vol_surface_slices(session, instrument=instrument, as_of=as_of)
    return [
        VolSurfaceTermStructurePoint(
            expiry_ts=s.expiry_ts, dte=s.dte, atm_iv=s.atm_iv
        )
        for s in full.slices
    ]


class NowcastingProjectionOut(BaseModel):
    """One per-release projection row."""

    release_id: str
    target_fred: str
    name: str
    method_id: str
    pred_mean: float | None
    pred_var: float | None
    last_actual: float | None
    surprise_z: float | None
    affected_instruments: list[str]
    fit_as_of: datetime | None


@router.get(
    "/nowcasting/projections", response_model=list[NowcastingProjectionOut]
)
def nowcasting_projections(
    session: SessionDep,
    method_id: str = Query(default="nowcasting.ols_ar.v1"),
    as_of: datetime | None = Query(default=None),
) -> list[NowcastingProjectionOut]:
    """Latest per-release nowcast projections. Reads the fitted
    state cached in ``system.methods_registry.serialized_blob`` (per
    weekly refit asset) and surfaces each release's pred_mean +
    pred_var + last_actual + standardised surprise.
    """
    if method_id not in ("nowcasting.ols_ar.v1", "nowcasting.bvar.v1"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown nowcasting method_id {method_id!r}",
        )
    from macro_trader.signals.nowcasting.refit import (
        load_bvar_state,
        load_ols_ar_state,
    )
    from macro_trader.signals.nowcasting.releases import DEFAULT_RELEASES

    method = (
        load_ols_ar_state(session)
        if method_id == "nowcasting.ols_ar.v1"
        else load_bvar_state(session)
    )
    if method is None or method._state is None:
        return []

    state = method._state
    fit_as_of = (
        datetime.fromisoformat(state["fit_as_of"])
        if state.get("fit_as_of")
        else None
    )
    per_release: dict[str, dict[str, Any]] = state["per_release"]
    spec_by_id = {r.release_id: r for r in DEFAULT_RELEASES}

    out: list[NowcastingProjectionOut] = []
    for release_id, fit in per_release.items():
        spec = spec_by_id.get(release_id)
        if spec is None:
            continue
        pred = _opt_float(fit.get("pred_mean"))
        last = _opt_float(fit.get("last_actual"))
        pred_var = _opt_float(fit.get("pred_var"))
        surprise_z: float | None = None
        if pred is not None and last is not None:
            denom = (pred_var or (abs(last) * 0.05 or 1e-6)) ** 0.5
            surprise_z = max(min((pred - last) / denom, 3.0), -3.0)
        out.append(
            NowcastingProjectionOut(
                release_id=release_id,
                target_fred=spec.target_fred,
                name=spec.name,
                method_id=method_id,
                pred_mean=pred,
                pred_var=pred_var,
                last_actual=last,
                surprise_z=surprise_z,
                affected_instruments=list(spec.affected_instruments),
                fit_as_of=fit_as_of,
            )
        )
    return out


class NowcastingHistoryPoint(BaseModel):
    """One observation in the historical nowcast vs actual series."""

    value_ts: datetime
    actual: float | None


@router.get(
    "/nowcasting/history", response_model=list[NowcastingHistoryPoint]
)
def nowcasting_history(
    session: SessionDep,
    release: str = Query(..., min_length=1, max_length=32),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> list[NowcastingHistoryPoint]:
    """Historical release values for one release. The vol-surface
    page's "nowcast vs actual" chart uses this together with
    nowcasting/projections for the latest forecast.

    Stage 5 ships only the actual-release series (no historical
    nowcast persistence yet); future stages can persist daily
    nowcasts to add the nowcast line.
    """
    from macro_trader.data.loaders import load_macro_series
    from macro_trader.signals.nowcasting.releases import DEFAULT_RELEASES

    spec = next((r for r in DEFAULT_RELEASES if r.release_id == release), None)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown release {release!r}",
        )
    target_to = to if to is not None else utcnow()
    target_from = from_ if from_ is not None else (
        target_to - timedelta(days=int(spec.frequency != "weekly") * 5 * 365)
    )
    series = load_macro_series(
        session,
        spec.target_fred,
        start=target_from,
        end=target_to,
        as_of=target_to,
    )
    return [
        NowcastingHistoryPoint(value_ts=ts, actual=float(v))
        for ts, v in series.items()
        if v is not None
    ]


class AltDataComponentRow(BaseModel):
    """Per-instrument breakdown by alt-data sub-signal."""

    instrument_id: str
    method_id: str
    raw_value: float | None
    confidence: float | None
    covered: bool
    extras: dict[str, Any]


@router.get(
    "/alt_data/components", response_model=list[AltDataComponentRow]
)
def alt_data_components(
    session: SessionDep,
    as_of: datetime | None = Query(default=None),
) -> list[AltDataComponentRow]:
    """Per-instrument breakdown of each alt-data sub-signal's
    latest raw_value + confidence + covered-flag. Drives the
    alt_data dashboard page's stacked view."""
    target = as_of if as_of is not None else utcnow()
    method_ids = (
        "alt_data.eia_storage.v1",
        "alt_data.usda_wasde.v1",
        "alt_data.google_trends.v1",
    )
    out: list[AltDataComponentRow] = []
    for method_id in method_ids:
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
        for r in session.scalars(stmt):
            meta = r.signal_metadata if isinstance(r.signal_metadata, dict) else {}
            covered = bool(meta.get("covered", True))
            extras: dict[str, Any] = {
                k: v
                for k, v in meta.items()
                if k not in {"covered", "method_id"}
            }
            out.append(
                AltDataComponentRow(
                    instrument_id=r.instrument_id,
                    method_id=method_id,
                    raw_value=_opt_float(r.raw_value),
                    confidence=_opt_float(r.confidence),
                    covered=covered,
                    extras=extras,
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
