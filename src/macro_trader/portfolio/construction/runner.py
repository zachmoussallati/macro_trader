"""Daily portfolio construction runner.

Sequencing:

1. Update the drawdown state (realised return + equity-curve row +
   gate evaluation) for the production portfolio method. This runs
   FIRST so the gate's scaling factor applies to today's
   positions.
2. Pull the latest composite_scores for the production composite,
   the latest covariance matrix for the production covariance
   method, and the active instrument universe.
3. Run every registered portfolio method on the shared input;
   multiply each method's target weights by the production-method
   drawdown scaling factor; persist to ``portfolio.positions``.
4. Cross-method comparator → ``system.method_comparisons``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.db.models.market_data import Instrument
from macro_trader.db.models.portfolio import (
    CovarianceEstimate,
    Position,
    VolatilityEstimate,
)
from macro_trader.db.models.signals import CompositeScore
from macro_trader.db.models.system import HeartbeatRow
from macro_trader.logging_setup import get_logger
from macro_trader.methods.comparator import run_comparisons_for_component
from macro_trader.portfolio.construction.comparator import (
    PortfolioConstructionComparator,
)
from macro_trader.portfolio.construction.methods import (
    DEFAULT_BLOCK_BY_ASSET_CLASS,
    BlackLittermanPortfolio,
    CVaRPortfolio,
    EqualRiskContributionPortfolio,
    HierarchicalRiskParityPortfolio,
    PortfolioInput,
    PortfolioMethod,
    PortfolioOutput,
)
from macro_trader.portfolio.drawdown.runner import (
    load_drawdown_state,
    update_drawdown_state,
)
from macro_trader.signals.designated import resolve_id
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_portfolio_methods() -> list[PortfolioMethod]:
    methods: list[PortfolioMethod] = [
        EqualRiskContributionPortfolio(),
        HierarchicalRiskParityPortfolio(),
        BlackLittermanPortfolio(),
        CVaRPortfolio(),
    ]
    return methods


def _active_instruments_with_blocks(session: Session) -> dict[str, str]:
    rows = list(
        session.scalars(
            select(Instrument).where(Instrument.is_active.is_(True))
        )
    )
    out: dict[str, str] = {}
    for r in rows:
        block = DEFAULT_BLOCK_BY_ASSET_CLASS.get(r.asset_class, r.asset_class)
        out[r.instrument_id] = block
    return out


def _latest_composite_scores(
    session: Session,
    *,
    composite_method_id: str,
    as_of: datetime,
) -> tuple[dict[str, float], dict[str, float]]:
    latest_obs = session.scalar(
        select(func.max(CompositeScore.observation_ts))
        .where(CompositeScore.method_id == composite_method_id)
        .where(CompositeScore.observation_ts <= as_of)
    )
    if latest_obs is None:
        return {}, {}
    rows = list(
        session.scalars(
            select(CompositeScore)
            .where(CompositeScore.method_id == composite_method_id)
            .where(CompositeScore.observation_ts == latest_obs)
        )
    )
    scores: dict[str, float] = {}
    confidence: dict[str, float] = {}
    # Latest value_ts per instrument.
    by_inst: dict[str, CompositeScore] = {}
    for r in rows:
        prev = by_inst.get(r.instrument_id)
        if prev is None or r.value_ts > prev.value_ts:
            by_inst[r.instrument_id] = r
    for inst, r in by_inst.items():
        if r.score is None:
            continue
        scores[inst] = float(r.score)
        confidence[inst] = float(r.confidence) if r.confidence is not None else 0.5
    return scores, confidence


def _latest_covariance(
    session: Session,
    *,
    cov_method_id: str,
    instrument_ids: list[str],
    as_of: datetime,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Returns (cov, ann_vols) ordered by ``instrument_ids``.

    Pulls the most-recent covariance + vol rows at or before
    ``as_of``. Returns None when the panel is incomplete (missing
    pairs).
    """
    if not instrument_ids:
        return None
    latest_cov_ts = session.scalar(
        select(func.max(CovarianceEstimate.as_of))
        .where(CovarianceEstimate.method_id == cov_method_id)
        .where(CovarianceEstimate.as_of <= as_of)
    )
    if latest_cov_ts is None:
        return None
    cov_rows = list(
        session.scalars(
            select(CovarianceEstimate)
            .where(CovarianceEstimate.method_id == cov_method_id)
            .where(CovarianceEstimate.as_of == latest_cov_ts)
        )
    )
    n = len(instrument_ids)
    idx = {inst: i for i, inst in enumerate(instrument_ids)}
    cov = np.zeros((n, n))
    seen = np.zeros((n, n), dtype=bool)
    for r in cov_rows:
        if r.instrument_a not in idx or r.instrument_b not in idx:
            continue
        i, j = idx[r.instrument_a], idx[r.instrument_b]
        cov[i, j] = float(r.covariance) if r.covariance is not None else 0.0
        seen[i, j] = True
    # Require every diagonal entry; missing off-diagonals default to 0.
    if not seen.diagonal().all():
        return None

    vol_rows = list(
        session.scalars(
            select(VolatilityEstimate)
            .where(VolatilityEstimate.method_id == cov_method_id)
            .where(VolatilityEstimate.as_of == latest_cov_ts)
            .where(VolatilityEstimate.instrument_id.in_(instrument_ids))
        )
    )
    vols = np.zeros(n)
    for r in vol_rows:
        if r.instrument_id in idx:
            vols[idx[r.instrument_id]] = float(r.volatility or 0.0)
    return cov, vols


def persist_positions(
    session: Session,
    method_id: str,
    output: PortfolioOutput,
    *,
    gate_level: str,
    gate_scaling: float,
    pre_gate_outputs: PortfolioOutput | None = None,
    lineage_id: uuid.UUID | None = None,
) -> int:
    if not output.positions:
        return 0
    pre_gate_by_inst: dict[str, float] = {}
    if pre_gate_outputs is not None:
        for p in pre_gate_outputs.positions:
            pre_gate_by_inst[p.instrument_id] = float(p.target_weight)
    rows = [
        {
            "method_id": method_id,
            "as_of": output.as_of,
            "instrument_id": p.instrument_id,
            "target_weight": float(p.target_weight),
            "pre_gate_weight": pre_gate_by_inst.get(p.instrument_id, float(p.target_weight)),
            "expected_vol_contribution": float(p.expected_vol_contribution),
            "composite_score": float(p.composite_score),
            "block": p.block,
            "gate_level": gate_level,
            "gate_scaling_factor": float(gate_scaling),
            "position_metadata": {
                **(output.metadata or {}),
                "expected_portfolio_vol": float(output.expected_portfolio_vol),
                "block_exposure": output.block_exposure,
            },
            "lineage_id": lineage_id,
        }
        for p in output.positions
    ]
    stmt = pg_insert(Position).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["method_id", "as_of", "instrument_id"],
        set_={
            "target_weight": stmt.excluded.target_weight,
            "pre_gate_weight": stmt.excluded.pre_gate_weight,
            "expected_vol_contribution": stmt.excluded.expected_vol_contribution,
            "composite_score": stmt.excluded.composite_score,
            "block": stmt.excluded.block,
            "gate_level": stmt.excluded.gate_level,
            "gate_scaling_factor": stmt.excluded.gate_scaling_factor,
            "position_metadata": stmt.excluded.position_metadata,
        },
    )
    result = session.execute(stmt)
    return int(getattr(result, "rowcount", None) or len(rows))


def _apply_gate_to_output(
    output: PortfolioOutput,
    *,
    scaling: float,
) -> PortfolioOutput:
    """Return a copy of ``output`` with each position's weight scaled."""
    if scaling >= 1.0 or not output.positions:
        return output
    scaled_positions = [
        type(p)(
            instrument_id=p.instrument_id,
            target_weight=float(p.target_weight * scaling),
            expected_vol_contribution=float(p.expected_vol_contribution),
            composite_score=p.composite_score,
            block=p.block,
            metadata={**(p.metadata or {}), "gate_scaling_factor": scaling},
        )
        for p in output.positions
    ]
    return type(output)(
        as_of=output.as_of,
        method_id=output.method_id,
        positions=scaled_positions,
        expected_portfolio_vol=float(output.expected_portfolio_vol * scaling),
        block_exposure={
            k: float(v) for k, v in output.block_exposure.items()
        },
        metadata={**(output.metadata or {}), "post_gate_scaling": scaling},
    )


def run_daily_portfolio(
    session: Session,
    *,
    as_of: datetime | None = None,
    methods: Iterable[PortfolioMethod] | None = None,
) -> dict[str, int]:
    """Daily portfolio job.

    Returns per-method row counts. Idempotent on the natural key
    via on-conflict-update.
    """
    now = as_of or utcnow()
    method_list = list(methods) if methods is not None else default_portfolio_methods()
    production_method_id = (
        resolve_id("portfolio_construction") or "portfolio.erc.v1"
    )

    # 1. Drawdown update for the production method first so subsequent
    # persists tag positions with the new gate state.
    new_state = update_drawdown_state(
        session, method_id=production_method_id, as_of=now
    )
    gate_level = new_state.current_level
    gate_scaling = new_state.effective_scaling_factor

    instruments_blocks = _active_instruments_with_blocks(session)
    instrument_ids = list(instruments_blocks.keys())
    if not instrument_ids:
        return {}

    composite_method_id = (
        resolve_id("composite_score") or "composite.linear.v1"
    )
    cov_method_id = (
        resolve_id("covariance_estimate") or "covariance.ledoit_wolf.v1"
    )

    scores, confidence = _latest_composite_scores(
        session, composite_method_id=composite_method_id, as_of=now
    )
    if not scores:
        log.info("portfolio.daily.no_composite_scores")
        return {}
    cov_panel = _latest_covariance(
        session, cov_method_id=cov_method_id, instrument_ids=instrument_ids, as_of=now
    )
    if cov_panel is None:
        log.info("portfolio.daily.no_covariance")
        return {}
    cov_matrix, vols = cov_panel

    inp = PortfolioInput(
        as_of=now,
        instrument_ids=instrument_ids,
        composite_scores=scores,
        composite_confidence=confidence,
        covariance_matrix=cov_matrix,
        annualised_vols=vols,
        blocks=instruments_blocks,
    )

    written: dict[str, int] = {}
    for method in method_list:
        out = method.compute(inp, session)
        # Each method's positions get scaled by the production
        # method's gate factor (one gate applies system-wide).
        gated = (
            _apply_gate_to_output(out, scaling=gate_scaling) if gate_scaling < 1.0 else out
        )
        written[method.metadata.method_id] = persist_positions(
            session,
            method.metadata.method_id,
            gated,
            gate_level=gate_level,
            gate_scaling=gate_scaling,
            pre_gate_outputs=out,
        )

    # Cross-method comparator on the construction component.
    run_comparisons_for_component(
        "portfolio_construction",
        cast(Any, PortfolioConstructionComparator()),
        inp,
        period_start=now - timedelta(days=1),
        period_end=now,
        notes="daily portfolio construction comparison",
        session=session,
    )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="portfolio.construction",
            meta={
                "rows_written": written,
                "gate_level": gate_level,
                "gate_scaling": gate_scaling,
            },
        )
    )
    session.flush()
    log.info(
        "portfolio.daily_run.complete",
        rows=written,
        gate=gate_level,
        scaling=gate_scaling,
    )
    return written


__all__ = [
    "default_portfolio_methods",
    "persist_positions",
    "run_daily_portfolio",
]


_ = load_drawdown_state, desc  # pragma: no cover - exported through submodules
