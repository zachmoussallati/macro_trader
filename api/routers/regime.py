"""Regime classifier API (Stage 6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from api.deps import SessionDep
from macro_trader.db.models.regime import RegimeAttribution, RegimeState
from macro_trader.db.models.system import MethodRegistryRow
from macro_trader.utils.dates import utcnow

router = APIRouter(prefix="/regime", tags=["regime"])


def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class RegimeMethodOut(BaseModel):
    method_id: str
    name: str
    status: str


@router.get("/methods", response_model=list[RegimeMethodOut])
def list_regime_methods(session: SessionDep) -> list[RegimeMethodOut]:
    """All registered methods under the regime_classifier component."""
    rows = list(
        session.scalars(
            select(MethodRegistryRow).where(
                MethodRegistryRow.component == "regime_classifier"
            )
        )
    )
    return [
        RegimeMethodOut(method_id=r.method_id, name=r.name, status=r.status.value)
        for r in rows
    ]


class RegimeCurrentOut(BaseModel):
    method_id: str
    value_ts: datetime
    observation_ts: datetime
    label: str
    probability_vector: dict[str, float]
    confidence: float | None
    transition_prob: float | None
    days_in_regime: int | None


@router.get("/current", response_model=RegimeCurrentOut | None)
def regime_current(
    session: SessionDep,
    method_id: str = Query(default="regime.rules.v1"),
    as_of: datetime | None = Query(default=None),
) -> RegimeCurrentOut | None:
    """Latest regime state for the chosen method at or before
    ``as_of``."""
    target = as_of if as_of is not None else utcnow()
    latest_obs = session.scalar(
        select(func.max(RegimeState.observation_ts))
        .where(RegimeState.method_id == method_id)
        .where(RegimeState.observation_ts <= target)
    )
    if latest_obs is None:
        return None
    row = session.scalar(
        select(RegimeState)
        .where(RegimeState.method_id == method_id)
        .where(RegimeState.observation_ts == latest_obs)
        .order_by(desc(RegimeState.value_ts))
        .limit(1)
    )
    if row is None:
        return None
    return RegimeCurrentOut(
        method_id=row.method_id,
        value_ts=row.value_ts,
        observation_ts=row.observation_ts,
        label=row.label,
        probability_vector={k: float(v) for k, v in (row.probability_vector or {}).items()},
        confidence=_opt_float(row.confidence),
        transition_prob=_opt_float(row.transition_prob),
        days_in_regime=row.days_in_regime,
    )


class RegimeHistoryPoint(BaseModel):
    value_ts: datetime
    label: str
    confidence: float | None
    transition_prob: float | None


@router.get("/history", response_model=list[RegimeHistoryPoint])
def regime_history(
    session: SessionDep,
    method_id: str = Query(default="regime.rules.v1"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> list[RegimeHistoryPoint]:
    """Per-day regime label series for one method.

    Aggregates to one row per ``value_ts`` by keeping the latest
    ``observation_ts`` per day.
    """
    stmt = (
        select(RegimeState)
        .where(RegimeState.method_id == method_id)
        .order_by(RegimeState.value_ts, desc(RegimeState.observation_ts))
    )
    if from_ is not None:
        stmt = stmt.where(RegimeState.value_ts >= from_)
    if to is not None:
        stmt = stmt.where(RegimeState.value_ts <= to)
    rows = list(session.scalars(stmt))

    seen: set[datetime] = set()
    out: list[RegimeHistoryPoint] = []
    for r in rows:
        if r.value_ts in seen:
            continue
        seen.add(r.value_ts)
        out.append(
            RegimeHistoryPoint(
                value_ts=r.value_ts,
                label=r.label,
                confidence=_opt_float(r.confidence),
                transition_prob=_opt_float(r.transition_prob),
            )
        )
    out.sort(key=lambda p: p.value_ts)
    return out


class RegimeAttributionRow(BaseModel):
    regime_method_id: str
    regime_label: str
    signal_method_id: str
    value_ts: datetime
    n_observations: int
    mean_return: float | None
    sharpe: float | None
    hit_rate: float | None


@router.get("/attribution", response_model=list[RegimeAttributionRow])
def regime_attribution(
    session: SessionDep,
    regime_method: str | None = Query(default=None),
    signal_method: str | None = Query(default=None),
    as_of: datetime | None = Query(default=None),
) -> list[RegimeAttributionRow]:
    """Per-(regime_method, regime_label, signal_method) attribution.

    Returns the latest row per triple at-or-before ``as_of``.
    """
    target = as_of if as_of is not None else utcnow()
    stmt = (
        select(RegimeAttribution)
        .where(RegimeAttribution.value_ts <= target)
        .order_by(
            RegimeAttribution.regime_method_id,
            RegimeAttribution.regime_label,
            RegimeAttribution.signal_method_id,
            desc(RegimeAttribution.value_ts),
        )
    )
    if regime_method:
        stmt = stmt.where(RegimeAttribution.regime_method_id == regime_method)
    if signal_method:
        stmt = stmt.where(RegimeAttribution.signal_method_id == signal_method)

    rows = list(session.scalars(stmt))
    seen: set[tuple[str, str, str]] = set()
    out: list[RegimeAttributionRow] = []
    for r in rows:
        key = (r.regime_method_id, r.regime_label, r.signal_method_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            RegimeAttributionRow(
                regime_method_id=r.regime_method_id,
                regime_label=r.regime_label,
                signal_method_id=r.signal_method_id,
                value_ts=r.value_ts,
                n_observations=r.n_observations,
                mean_return=_opt_float(r.mean_return),
                sharpe=_opt_float(r.sharpe),
                hit_rate=_opt_float(r.hit_rate),
            )
        )
    return out


class ChangepointPoint(BaseModel):
    value_ts: datetime
    changepoint_probability: float


@router.get("/changepoints", response_model=list[ChangepointPoint])
def regime_changepoints(
    session: SessionDep,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> list[ChangepointPoint]:
    """Time series of BOCPD changepoint probabilities."""
    stmt = (
        select(RegimeState)
        .where(RegimeState.method_id == "regime.bocpd.v1")
        .order_by(RegimeState.value_ts, desc(RegimeState.observation_ts))
    )
    if from_ is not None:
        stmt = stmt.where(RegimeState.value_ts >= from_)
    if to is not None:
        stmt = stmt.where(RegimeState.value_ts <= to)
    rows = list(session.scalars(stmt))
    if not rows and (from_ is not None or to is not None):
        # Surface 400 when client asks for a method we know isn't
        # going to populate (BOCPD requires Stage 6 daily run).
        pass
    seen: set[datetime] = set()
    out: list[ChangepointPoint] = []
    for r in rows:
        if r.value_ts in seen:
            continue
        seen.add(r.value_ts)
        cp = _opt_float(r.transition_prob)
        if cp is None:
            continue
        out.append(ChangepointPoint(value_ts=r.value_ts, changepoint_probability=cp))
    out.sort(key=lambda p: p.value_ts)
    _ = HTTPException, status  # silence unused
    return out
