"""Composite scoring API (Stage 7).

5 endpoints:

- ``GET /composite/methods``                         — list registered composite methods.
- ``GET /composite/scores``                          — per-instrument scores for ``as_of``.
- ``GET /composite/breakdown?instrument=...``        — per-signal contribution to one score.
- ``GET /composite/weights``                         — current effective per-signal weights.
- ``GET /composite/transition_multiplier``           — current conviction-dampening factor.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from api.deps import SessionDep
from macro_trader.composite.transition import (
    latest_changepoint_probability,
    transition_multiplier_from_probability,
)
from macro_trader.composite.weights import (
    effective_weights_for_probabilities,
    load_latest_weight_snapshot,
)
from macro_trader.db.models.regime import RegimeState
from macro_trader.db.models.signals import CompositeScore, CompositeWeight
from macro_trader.db.models.system import MethodRegistryRow
from macro_trader.utils.dates import utcnow

router = APIRouter(prefix="/composite", tags=["composite"])


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
class CompositeMethodOut(BaseModel):
    method_id: str
    name: str
    status: str


@router.get("/methods", response_model=list[CompositeMethodOut])
def list_composite_methods(session: SessionDep) -> list[CompositeMethodOut]:
    rows = list(
        session.scalars(
            select(MethodRegistryRow).where(
                MethodRegistryRow.component == "composite_score"
            )
        )
    )
    return [
        CompositeMethodOut(
            method_id=r.method_id, name=r.name, status=r.status.value
        )
        for r in rows
    ]


# ----------------------------------------------------------------------
# /scores
# ----------------------------------------------------------------------
class CompositeScoreOut(BaseModel):
    method_id: str
    instrument_id: str
    value_ts: datetime
    observation_ts: datetime
    raw_score: float | None
    score: float | None
    confidence: float | None
    regime_label: str | None
    n_signals_used: int | None


@router.get("/scores", response_model=list[CompositeScoreOut])
def composite_scores(
    session: SessionDep,
    method_id: str = Query(default="composite.linear.v1"),
    as_of: datetime | None = Query(default=None),
    sort_desc: bool = Query(default=True),
) -> list[CompositeScoreOut]:
    """Per-instrument composite scores for the latest observation_ts
    at or before ``as_of``. Default sorted score-descending."""
    target = as_of if as_of is not None else utcnow()
    latest_obs = session.scalar(
        select(func.max(CompositeScore.observation_ts))
        .where(CompositeScore.method_id == method_id)
        .where(CompositeScore.observation_ts <= target)
    )
    if latest_obs is None:
        return []
    rows = list(
        session.scalars(
            select(CompositeScore)
            .where(CompositeScore.method_id == method_id)
            .where(CompositeScore.observation_ts == latest_obs)
        )
    )
    # Keep one row per instrument: latest value_ts for each.
    by_inst: dict[str, CompositeScore] = {}
    for r in rows:
        prev = by_inst.get(r.instrument_id)
        if prev is None or r.value_ts > prev.value_ts:
            by_inst[r.instrument_id] = r
    out = [
        CompositeScoreOut(
            method_id=r.method_id,
            instrument_id=r.instrument_id,
            value_ts=r.value_ts,
            observation_ts=r.observation_ts,
            raw_score=_opt_float(r.raw_score),
            score=_opt_float(r.score),
            confidence=_opt_float(r.confidence),
            regime_label=r.regime_label,
            n_signals_used=r.n_signals_used,
        )
        for r in by_inst.values()
    ]
    out.sort(
        key=lambda p: (p.score if p.score is not None else 0.0),
        reverse=sort_desc,
    )
    return out


# ----------------------------------------------------------------------
# /breakdown
# ----------------------------------------------------------------------
class CompositeContribution(BaseModel):
    signal_method_id: str
    weight: float
    z: float
    confidence: float
    contribution: float


class CompositeBreakdownOut(BaseModel):
    method_id: str
    instrument_id: str
    value_ts: datetime
    observation_ts: datetime
    score: float | None
    raw_score: float | None
    regime_label: str | None
    transition_multiplier: float | None
    transition_probability: float | None
    contributions: list[CompositeContribution]


@router.get(
    "/breakdown",
    response_model=CompositeBreakdownOut | None,
)
def composite_breakdown(
    session: SessionDep,
    instrument: str = Query(...),
    method_id: str = Query(default="composite.linear.v1"),
    as_of: datetime | None = Query(default=None),
) -> CompositeBreakdownOut | None:
    """Per-signal contribution decomposition of one composite score."""
    target = as_of if as_of is not None else utcnow()
    row = session.scalar(
        select(CompositeScore)
        .where(CompositeScore.method_id == method_id)
        .where(CompositeScore.instrument_id == instrument)
        .where(CompositeScore.observation_ts <= target)
        .order_by(
            desc(CompositeScore.observation_ts),
            desc(CompositeScore.value_ts),
        )
        .limit(1)
    )
    if row is None:
        return None
    meta = row.composite_metadata or {}
    contributions = [
        CompositeContribution(
            signal_method_id=str(c.get("signal_method_id", "")),
            weight=float(c.get("weight", 0.0)),
            z=float(c.get("z", 0.0)),
            confidence=float(c.get("confidence", 0.0)),
            contribution=float(c.get("contribution", 0.0)),
        )
        for c in meta.get("contributions", [])
    ]
    return CompositeBreakdownOut(
        method_id=row.method_id,
        instrument_id=row.instrument_id,
        value_ts=row.value_ts,
        observation_ts=row.observation_ts,
        score=_opt_float(row.score),
        raw_score=_opt_float(row.raw_score),
        regime_label=row.regime_label,
        transition_multiplier=_opt_float(meta.get("transition_multiplier")),
        transition_probability=_opt_float(meta.get("transition_probability")),
        contributions=contributions,
    )


# ----------------------------------------------------------------------
# /weights
# ----------------------------------------------------------------------
class CompositeWeightOut(BaseModel):
    regime_label: str
    signal_method_id: str
    weight: float
    weight_source: str


class EffectiveWeightOut(BaseModel):
    signal_method_id: str
    effective_weight: float


class CompositeWeightsOut(BaseModel):
    method_id: str
    snapshot_ts: datetime | None
    per_regime: list[CompositeWeightOut]
    effective: list[EffectiveWeightOut]
    regime_probability_vector: dict[str, float] | None


@router.get("/weights", response_model=CompositeWeightsOut)
def composite_weights(
    session: SessionDep,
    method_id: str = Query(default="composite.linear.v1"),
    regime_method_id: str = Query(default="regime.rules.v1"),
    as_of: datetime | None = Query(default=None),
) -> CompositeWeightsOut:
    """Current effective per-signal weights with regime decomposition."""
    target = as_of if as_of is not None else utcnow()
    snap_ts = session.scalar(
        select(CompositeWeight.snapshot_ts)
        .where(CompositeWeight.method_id == method_id)
        .where(CompositeWeight.snapshot_ts <= target)
        .order_by(desc(CompositeWeight.snapshot_ts))
        .limit(1)
    )
    per_regime: list[CompositeWeightOut] = []
    effective: list[EffectiveWeightOut] = []
    prob_vec: dict[str, float] | None = None
    if snap_ts is not None:
        rows = list(
            session.scalars(
                select(CompositeWeight)
                .where(CompositeWeight.method_id == method_id)
                .where(CompositeWeight.snapshot_ts == snap_ts)
            )
        )
        per_regime = [
            CompositeWeightOut(
                regime_label=r.regime_label,
                signal_method_id=r.signal_method_id,
                weight=float(r.weight),
                weight_source=r.weight_source,
            )
            for r in rows
        ]
        # Pull the latest regime probability vector for the configured
        # regime method.
        regime_row = session.scalar(
            select(RegimeState)
            .where(RegimeState.method_id == regime_method_id)
            .where(RegimeState.observation_ts <= target)
            .order_by(desc(RegimeState.value_ts), desc(RegimeState.observation_ts))
            .limit(1)
        )
        if regime_row is not None and regime_row.probability_vector:
            prob_vec = {
                k: float(v) for k, v in regime_row.probability_vector.items()
            }
            snapshot_df = load_latest_weight_snapshot(
                session, composite_method_id=method_id, as_of=target
            )
            eff = effective_weights_for_probabilities(snapshot_df, prob_vec)
            effective = [
                EffectiveWeightOut(signal_method_id=k, effective_weight=v)
                for k, v in sorted(eff.items(), key=lambda kv: -kv[1])
            ]
    return CompositeWeightsOut(
        method_id=method_id,
        snapshot_ts=snap_ts,
        per_regime=per_regime,
        effective=effective,
        regime_probability_vector=prob_vec,
    )


# ----------------------------------------------------------------------
# /transition_multiplier
# ----------------------------------------------------------------------
class TransitionMultiplierOut(BaseModel):
    multiplier: float
    changepoint_probability: float | None
    threshold: float
    floor: float


@router.get("/transition_multiplier", response_model=TransitionMultiplierOut)
def composite_transition_multiplier(
    session: SessionDep,
    as_of: datetime | None = Query(default=None),
    bocpd_method_id: str = Query(default="regime.bocpd.v1"),
    threshold: float = Query(default=0.5, ge=0.0, le=1.0),
    floor: float = Query(default=0.5, ge=0.0, le=1.0),
) -> TransitionMultiplierOut:
    target = as_of if as_of is not None else utcnow()
    prob = latest_changepoint_probability(
        session, bocpd_method_id=bocpd_method_id, as_of=target
    )
    multiplier = transition_multiplier_from_probability(
        prob, threshold=threshold, floor=floor
    )
    return TransitionMultiplierOut(
        multiplier=multiplier,
        changepoint_probability=prob,
        threshold=threshold,
        floor=floor,
    )
