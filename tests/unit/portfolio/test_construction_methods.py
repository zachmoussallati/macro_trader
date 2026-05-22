"""Smoke tests for the 4 portfolio construction methods.

Pure-Python tests with a synthetic in-memory PortfolioInput. None
of the methods touch the DB or sqlalchemy; the runner integration is
exercised by ``tests/integration/portfolio/test_portfolio_pipeline.py``.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pytest

from macro_trader.portfolio.construction.methods import (
    BlackLittermanPortfolio,
    ConstraintConfig,
    CVaRPortfolio,
    EqualRiskContributionPortfolio,
    HierarchicalRiskParityPortfolio,
    PortfolioInput,
    PortfolioMethod,
)


def _synthetic_input(
    *,
    n: int = 13,
    seed: int = 7,
) -> PortfolioInput:
    """Build a 13-instrument synthetic input with mixed-sign
    composite scores, plausible per-block correlation structure, and
    enough budget for the per-position + block caps to be feasible."""
    instrument_ids = [
        "CL", "BZ", "NG", "HO", "RB",
        "HG", "ALI",
        "GC", "SI", "PL",
        "ZC", "ZS", "ZW",
    ][:n]
    rng = np.random.default_rng(seed)
    # Annualised vols around 25%.
    vols = np.array([0.30, 0.30, 0.45, 0.30, 0.30, 0.30, 0.25, 0.18, 0.28, 0.22, 0.25, 0.25, 0.28])[:n]
    # Block-aware correlation with intra-block 0.6 and cross-block 0.15.
    blocks_seq = [
        "energy", "energy", "energy", "energy", "energy",
        "base_metals", "base_metals",
        "precious_metals", "precious_metals", "precious_metals",
        "grains", "grains", "grains",
    ][:n]
    corr = np.full((n, n), 0.15)
    for i in range(n):
        for j in range(n):
            if i == j:
                corr[i, j] = 1.0
            elif blocks_seq[i] == blocks_seq[j]:
                corr[i, j] = 0.60
    cov = corr * np.outer(vols, vols)

    scores: dict[str, float] = {}
    confs: dict[str, float] = {}
    blocks: dict[str, str] = {}
    for i, inst in enumerate(instrument_ids):
        # Mix of strong + weak scores across both directions; some
        # below threshold to test the eligibility filter.
        scores[inst] = float(rng.uniform(-0.6, 0.6))
        confs[inst] = float(rng.uniform(0.3, 0.9))
        blocks[inst] = blocks_seq[i]

    return PortfolioInput(
        as_of=datetime(2026, 5, 22),
        instrument_ids=instrument_ids,
        composite_scores=scores,
        composite_confidence=confs,
        covariance_matrix=cov,
        annualised_vols=vols,
        blocks=blocks,
    )


@pytest.mark.parametrize(
    "method_factory",
    [
        EqualRiskContributionPortfolio,
        HierarchicalRiskParityPortfolio,
        BlackLittermanPortfolio,
        CVaRPortfolio,
    ],
)
def test_method_produces_output_at_target_vol(method_factory) -> None:
    """Every method should produce non-zero positions on a feasible
    synthetic input, vol-target to the configured target_portfolio_vol."""
    cfg = ConstraintConfig(target_portfolio_vol=0.12)
    method: PortfolioMethod = method_factory(constraints=cfg)
    out = method.compute(_synthetic_input(), session=object())
    assert out.positions, f"{method.metadata.method_id} produced no positions"
    # Each position is signed; sign should match composite_score sign.
    for p in out.positions:
        if p.target_weight != 0.0:
            assert (
                (p.target_weight > 0 and p.composite_score > 0)
                or (p.target_weight < 0 and p.composite_score < 0)
            ), f"{method.metadata.method_id} sign mismatch on {p.instrument_id}"
    # Realised portfolio vol close to target. Tolerance generous
    # because cap-clipping after vol-scaling can break exact match.
    assert math.isclose(
        out.expected_portfolio_vol, 0.12, rel_tol=0.10
    ), f"{method.metadata.method_id} vol {out.expected_portfolio_vol:.4f} != 0.12"


@pytest.mark.parametrize(
    "method_factory",
    [
        EqualRiskContributionPortfolio,
        HierarchicalRiskParityPortfolio,
        BlackLittermanPortfolio,
        CVaRPortfolio,
    ],
)
def test_method_respects_block_caps(method_factory) -> None:
    """No single block should exceed the configured max_block_weight
    in the *normalised* (gross-exposure) sense."""
    cfg = ConstraintConfig(target_portfolio_vol=0.12, max_block_weight=0.40)
    method: PortfolioMethod = method_factory(constraints=cfg)
    out = method.compute(_synthetic_input(), session=object())
    if not out.positions:
        return
    gross = sum(abs(p.target_weight) for p in out.positions)
    block_totals: dict[str, float] = {}
    for p in out.positions:
        key = p.block or "unknown"
        block_totals[key] = block_totals.get(key, 0.0) + abs(p.target_weight)
    if gross == 0:
        return
    for block, total in block_totals.items():
        share = total / gross
        # 5% tolerance to absorb floating-point + vol-scaling drift.
        assert share <= cfg.max_block_weight + 0.05, (
            f"{method.metadata.method_id} block {block} share "
            f"{share:.3f} exceeds cap {cfg.max_block_weight:.2f}"
        )


def test_eligibility_filter_drops_weak_scores() -> None:
    """Positions with |composite_score| < min_composite_score_threshold
    are filtered out."""
    cfg = ConstraintConfig(min_composite_score_threshold=0.10)
    inp = PortfolioInput(
        as_of=datetime(2026, 5, 22),
        instrument_ids=["CL", "GC", "NG"],
        composite_scores={"CL": 0.6, "GC": 0.05, "NG": -0.4},  # GC below threshold
        composite_confidence={"CL": 0.8, "GC": 0.5, "NG": 0.7},
        covariance_matrix=np.diag([0.09, 0.04, 0.20]),
        annualised_vols=np.array([0.30, 0.20, 0.45]),
        blocks={"CL": "energy", "GC": "precious_metals", "NG": "energy"},
    )
    method = EqualRiskContributionPortfolio(constraints=cfg)
    out = method.compute(inp, session=object())
    inst_ids = [p.instrument_id for p in out.positions]
    # GC under threshold AND len(eligible) = 2 < min_positions=3 -> no positions.
    assert "GC" not in inst_ids
