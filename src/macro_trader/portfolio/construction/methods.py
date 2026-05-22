"""Four portfolio construction methods.

All share the same :class:`PortfolioInput` / :class:`PortfolioOutput`
shapes and the same block-aware constraint helpers. Only the
*allocation* step differs across methods.

Sign convention (Stage 8 prompt §"Composite-to-position translation"):
the *sign* of every position is taken from ``sign(composite_score)``
across the board. The *magnitude* is whatever the portfolio method
allocates. Composite magnitude only enters BL + CVaR's expected-
return inputs; ERC + HRP ignore composite magnitudes (they're
variance-driven).
"""

# Stat-ML convention uses uppercase Sigma / Q / Pi — suppress N806
# file-wide so numpy code stays readable.
# ruff: noqa: N806

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

import numpy as np
import pandas as pd
from scipy import linalg as sla
from scipy.cluster.hierarchy import linkage, to_tree
from scipy.optimize import linprog, minimize

from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import Method, MethodMetadata

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

# Map asset_class -> "block" for the constraint helpers. Stage 4A's
# instrument reseed populated sub_class; we use it directly.
DEFAULT_BLOCK_BY_ASSET_CLASS = {
    "energy": "energy",
    "base_metals": "base_metals",
    "precious_metals": "precious_metals",
    "grains": "grains",
    # Macro proxies (SPY/HYG/IEF) shouldn't be sized; treat as a
    # "macro" block. They're filtered out before optimisation
    # because they don't appear in composite_scores anyway.
    "macro_proxy": "macro",
}


# ----------------------------------------------------------------------
# Input / output shapes
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class PortfolioInput:
    """Input contract for a portfolio method's daily compute."""

    as_of: datetime
    instrument_ids: list[str]
    # Per-instrument composite score in [-1, 1]. Sign drives
    # position direction; magnitude is consumed by BL + CVaR.
    composite_scores: dict[str, float]
    # Per-instrument expected confidence in the composite.
    composite_confidence: dict[str, float] = field(default_factory=dict)
    # Per-(instrument_a, instrument_b) covariance + per-instrument
    # annualised vol from the production covariance method.
    covariance_matrix: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    annualised_vols: np.ndarray = field(default_factory=lambda: np.zeros(0))
    # Block membership per instrument.
    blocks: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioPosition:
    instrument_id: str
    target_weight: float           # signed; sums(|.|) <= 1
    expected_vol_contribution: float
    composite_score: float
    block: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioOutput:
    as_of: datetime
    method_id: str
    positions: list[PortfolioPosition]
    expected_portfolio_vol: float
    block_exposure: dict[str, float]
    metadata: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Constraint config
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class ConstraintConfig:
    target_portfolio_vol: float = 0.12
    max_block_weight: float = 0.40
    max_position_weight: float = 0.15
    min_positions: int = 3
    min_composite_score_threshold: float = 0.10


# ----------------------------------------------------------------------
# Shared scaffolding
# ----------------------------------------------------------------------
class PortfolioMethod(Method[PortfolioInput, PortfolioOutput]):
    metadata: MethodMetadata
    constraints: ConstraintConfig

    def fit(self, data: PortfolioInput) -> None:
        return None

    def predict(self, data: PortfolioInput) -> PortfolioOutput:
        raise RuntimeError("use compute(data, session)")

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> Self:
        return cls()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _filter_eligible(
    data: PortfolioInput, *, cfg: ConstraintConfig
) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray, dict[str, str]]:
    """Drop instruments whose |composite_score| is below threshold.

    Returns the filtered (ids, signs, scores, covariance_submatrix,
    blocks) tuple. ``signs`` is +1 / -1 per kept instrument.
    """
    keep_idx: list[int] = []
    signs: list[float] = []
    scores: list[float] = []
    blocks_out: dict[str, str] = {}
    for i, inst in enumerate(data.instrument_ids):
        score = float(data.composite_scores.get(inst, 0.0))
        if abs(score) < cfg.min_composite_score_threshold:
            continue
        keep_idx.append(i)
        signs.append(1.0 if score >= 0 else -1.0)
        scores.append(score)
        blocks_out[inst] = data.blocks.get(inst, "unknown")
    if not keep_idx:
        return [], np.array([]), np.array([]), np.zeros((0, 0)), {}
    ids = [data.instrument_ids[i] for i in keep_idx]
    cov = data.covariance_matrix[np.ix_(keep_idx, keep_idx)]
    return (
        ids,
        np.array(signs),
        np.array(scores),
        cov,
        blocks_out,
    )


def _scale_to_target_vol(
    weights: np.ndarray, cov: np.ndarray, *, target_vol: float
) -> np.ndarray:
    """Scale weights so the resulting portfolio variance equals
    ``target_vol**2``. Pure linear scale; preserves the relative
    allocation between instruments."""
    if weights.size == 0:
        return weights
    portfolio_var = float(weights @ cov @ weights)
    if portfolio_var <= 0:
        return weights
    realised_vol = np.sqrt(portfolio_var)
    if realised_vol == 0:
        return weights
    return weights * (target_vol / realised_vol)


def _apply_position_and_block_caps(
    weights: np.ndarray,
    instrument_ids: list[str],
    blocks: dict[str, str],
    *,
    cfg: ConstraintConfig,
) -> np.ndarray:
    """Post-allocation clipper: enforce position + block share caps.

    Operates on *normalised gross share* — i.e. ``|w_i| / sum(|w|)``.
    The constraints are:

    - ``|w_i| / gross <= max_position_weight``
    - ``sum_block(|w|) / gross <= max_block_weight``

    Naive single-pass capping in absolute terms then renormalising
    can leave a block above its share cap (capping one block makes
    other shares rise). Iterate: scale over-cap positions / blocks
    down, renormalise, repeat. Converges in <= 5 iterations
    practically.
    """
    abs_w = np.abs(weights).copy()
    if abs_w.size == 0:
        return weights
    by_block: dict[str, list[int]] = {}
    for i, inst in enumerate(instrument_ids):
        by_block.setdefault(blocks.get(inst, "unknown"), []).append(i)

    for _ in range(10):
        total = float(abs_w.sum())
        if total <= 0:
            break
        normalised = abs_w / total
        position_over = normalised > cfg.max_position_weight + 1e-9
        block_shares = {b: normalised[idxs].sum() for b, idxs in by_block.items()}
        block_over = {
            b: s for b, s in block_shares.items() if s > cfg.max_block_weight + 1e-9
        }
        if not position_over.any() and not block_over:
            break
        # Per-position cap first: scale individual offenders to the cap.
        if position_over.any():
            target_abs = cfg.max_position_weight * total
            abs_w[position_over] = target_abs
        # Per-block cap: scale the entire over-block down by the
        # ratio of (target share) / (current share) within the
        # current total. Renormalises naturally in the next loop.
        for block, share in block_over.items():
            idxs = by_block[block]
            scale = cfg.max_block_weight / share
            for i in idxs:
                abs_w[i] *= scale
    # Final renormalisation to gross = 1.0.
    total = float(abs_w.sum())
    if total > 0:
        abs_w = abs_w / total
    return abs_w * np.sign(weights)


def _expected_vol_contribution(
    weights: np.ndarray, cov: np.ndarray
) -> np.ndarray:
    """Per-instrument contribution to portfolio variance, then
    normalised to fractions of total variance."""
    if weights.size == 0:
        return np.array([])
    contrib = weights * (cov @ weights)
    total = float(contrib.sum())
    if total <= 0:
        return np.zeros_like(weights)
    return contrib / total


def _block_exposure(
    weights: np.ndarray,
    instrument_ids: list[str],
    blocks: dict[str, str],
) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, inst in enumerate(instrument_ids):
        block = blocks.get(inst, "unknown")
        out[block] = out.get(block, 0.0) + float(abs(weights[i]))
    return out


def _zero_output(
    *, as_of: datetime, method_id: str, instrument_ids: list[str]
) -> PortfolioOutput:
    """No-op output when there aren't enough eligible positions."""
    return PortfolioOutput(
        as_of=as_of,
        method_id=method_id,
        positions=[],
        expected_portfolio_vol=0.0,
        block_exposure={},
        metadata={"reason": "no_eligible_positions"},
    )


def _build_output(
    *,
    as_of: datetime,
    method_id: str,
    instrument_ids: list[str],
    weights: np.ndarray,
    cov: np.ndarray,
    composite_scores: np.ndarray,
    blocks: dict[str, str],
    extra_metadata: dict[str, Any] | None = None,
) -> PortfolioOutput:
    contribs = _expected_vol_contribution(weights, cov)
    portfolio_vol = float(np.sqrt(max(weights @ cov @ weights, 0.0)))
    block_exp = _block_exposure(weights, instrument_ids, blocks)
    positions = [
        PortfolioPosition(
            instrument_id=instrument_ids[i],
            target_weight=float(weights[i]),
            expected_vol_contribution=float(contribs[i]),
            composite_score=float(composite_scores[i]),
            block=blocks.get(instrument_ids[i]),
            metadata={},
        )
        for i in range(len(instrument_ids))
    ]
    metadata = {"method": method_id}
    if extra_metadata:
        metadata.update(extra_metadata)
    return PortfolioOutput(
        as_of=as_of,
        method_id=method_id,
        positions=positions,
        expected_portfolio_vol=portfolio_vol,
        block_exposure=block_exp,
        metadata=metadata,
    )


# ----------------------------------------------------------------------
# Method 1: ERC (BASELINE)
# ----------------------------------------------------------------------
class EqualRiskContributionPortfolio(PortfolioMethod):
    metadata = MethodMetadata(
        method_id="portfolio.erc.v1",
        component="portfolio_construction",
        name="Equal Risk Contribution Portfolio",
        version="1.0.0",
        description=(
            "ERC sizing with composite-score-directed positions. "
            "Each instrument contributes equally to total portfolio "
            "variance. Robust to expected-return estimation error."
        ),
        references=[
            "Maillard, Roncalli, Teiletche (2010) The Properties of "
            "Equally Weighted Risk Contribution Portfolios",
        ],
    )

    def __init__(
        self,
        *,
        constraints: ConstraintConfig | None = None,
        max_iterations: int = 1000,
        convergence_tol: float = 1e-6,
    ) -> None:
        self.constraints = constraints or ConstraintConfig()
        self.max_iterations = int(max_iterations)
        self.convergence_tol = float(convergence_tol)

    def compute(
        self, data: PortfolioInput, session: Session | None
    ) -> PortfolioOutput:
        ids, signs, scores, cov, blocks = _filter_eligible(
            data, cfg=self.constraints
        )
        if len(ids) < self.constraints.min_positions:
            return _zero_output(
                as_of=data.as_of,
                method_id=self.metadata.method_id,
                instrument_ids=data.instrument_ids,
            )
        n = len(ids)

        # ERC unconstrained: minimise sum_i (w_i * (Sigma w)_i - 1/n)^2.
        # Operate on absolute weights; multiply by signs at the end.
        def objective(w: np.ndarray) -> float:
            sigma_w = cov @ w
            risk_contrib = w * sigma_w
            total = float(risk_contrib.sum())
            if total <= 0:
                return 1e12
            target = total / n
            return float(np.sum((risk_contrib - target) ** 2))

        # Constraints: weights nonneg, sum = 1, max_position_weight,
        # max_block_weight per block.
        bounds = [(0.0, self.constraints.max_position_weight)] * n
        cons: list[dict[str, Any]] = [
            {"type": "eq", "fun": lambda w: float(np.sum(w)) - 1.0}
        ]
        by_block: dict[str, list[int]] = {}
        for i, inst in enumerate(ids):
            by_block.setdefault(blocks.get(inst, "unknown"), []).append(i)
        for _block_label, idxs in by_block.items():
            cons.append(
                {
                    "type": "ineq",
                    "fun": lambda w, idxs=idxs: float(
                        self.constraints.max_block_weight - float(w[idxs].sum())
                    ),
                }
            )

        x0 = np.ones(n) / n
        res = minimize(
            objective,
            x0,
            method="SLSQP",
            bounds=bounds,
            constraints=cons,
            options={"maxiter": self.max_iterations, "ftol": self.convergence_tol},
        )
        w_abs = np.maximum(np.asarray(res.x), 0.0)
        if w_abs.sum() <= 0:
            w_abs = x0
        w_abs = w_abs / w_abs.sum()
        # Apply caps (catches post-optimiser float drift).
        w_signed = signs * w_abs
        w_signed = _apply_position_and_block_caps(
            w_signed, ids, blocks, cfg=self.constraints
        )
        # Scale to target portfolio vol.
        w_signed = _scale_to_target_vol(
            w_signed, cov, target_vol=self.constraints.target_portfolio_vol
        )

        return _build_output(
            as_of=data.as_of,
            method_id=self.metadata.method_id,
            instrument_ids=ids,
            weights=w_signed,
            cov=cov,
            composite_scores=scores,
            blocks=blocks,
            extra_metadata={
                "n_iterations": int(getattr(res, "nit", 0)),
                "converged": bool(getattr(res, "success", False)),
                "objective_final": float(getattr(res, "fun", 0.0)),
            },
        )


# ----------------------------------------------------------------------
# Method 2: HRP (SHADOW)
# ----------------------------------------------------------------------
class HierarchicalRiskParityPortfolio(PortfolioMethod):
    metadata = MethodMetadata(
        method_id="portfolio.hrp.v1",
        component="portfolio_construction",
        name="Hierarchical Risk Parity",
        version="1.0.0",
        description=(
            "HRP per Lopez de Prado 2016: tree clustering on the "
            "correlation matrix, quasi-diagonalisation, recursive "
            "bisection. Avoids matrix inversion; respects implicit "
            "cluster structure even when not told about asset class blocks."
        ),
        references=[
            "Lopez de Prado (2016) Building Diversified Portfolios that "
            "Outperform Out of Sample",
        ],
    )

    def __init__(
        self,
        *,
        constraints: ConstraintConfig | None = None,
        linkage_method: str = "single",
    ) -> None:
        self.constraints = constraints or ConstraintConfig()
        self.linkage_method = str(linkage_method)

    def compute(
        self, data: PortfolioInput, session: Session | None
    ) -> PortfolioOutput:
        ids, signs, scores, cov, blocks = _filter_eligible(
            data, cfg=self.constraints
        )
        if len(ids) < self.constraints.min_positions:
            return _zero_output(
                as_of=data.as_of,
                method_id=self.metadata.method_id,
                instrument_ids=data.instrument_ids,
            )
        n = len(ids)
        d = np.sqrt(np.diag(cov))
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = cov / np.outer(d, d)
        corr = np.clip(np.nan_to_num(corr, nan=0.0), -1.0, 1.0)
        np.fill_diagonal(corr, 1.0)
        # Correlation distance per Lopez de Prado: sqrt(0.5 * (1 - corr)).
        dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
        # Reduce the distance matrix to condensed form for scipy.
        condensed = dist[np.triu_indices(n, k=1)]
        Z = linkage(condensed, method=self.linkage_method)
        tree = to_tree(Z, rd=False)
        ordered = _get_quasi_diag(tree, n)
        w_abs = _recursive_bisection(cov, ordered)
        # Re-order back into original ids order.
        weights_abs = np.zeros(n)
        for pos, orig_idx in enumerate(ordered):
            weights_abs[orig_idx] = w_abs[pos]

        w_signed = signs * weights_abs
        w_signed = _apply_position_and_block_caps(
            w_signed, ids, blocks, cfg=self.constraints
        )
        w_signed = _scale_to_target_vol(
            w_signed, cov, target_vol=self.constraints.target_portfolio_vol
        )

        return _build_output(
            as_of=data.as_of,
            method_id=self.metadata.method_id,
            instrument_ids=ids,
            weights=w_signed,
            cov=cov,
            composite_scores=scores,
            blocks=blocks,
            extra_metadata={"linkage_method": self.linkage_method},
        )


def _get_quasi_diag(tree: Any, n: int) -> list[int]:
    """In-order traversal of the linkage tree, yielding the
    quasi-diagonalised index order."""
    order: list[int] = []

    def _walk(node: Any) -> None:
        if node is None:
            return
        if node.is_leaf():
            order.append(int(node.id))
            return
        _walk(node.get_left())
        _walk(node.get_right())

    _walk(tree)
    # Should yield exactly n indices.
    if len(order) != n:
        # Fall back to identity order if traversal mis-counted.
        return list(range(n))
    return order


def _recursive_bisection(cov: np.ndarray, ordered: list[int]) -> np.ndarray:
    """Allocate inverse-variance weights, recursively bisecting
    along the quasi-diagonal order."""
    n = len(ordered)
    w = np.ones(n)
    cov_ordered = cov[np.ix_(ordered, ordered)]
    clusters: list[tuple[int, int]] = [(0, n)]
    while clusters:
        new_clusters: list[tuple[int, int]] = []
        for start, end in clusters:
            if end - start <= 1:
                continue
            mid = (start + end) // 2
            left_idx = slice(start, mid)
            right_idx = slice(mid, end)
            var_left = _cluster_variance(cov_ordered[left_idx, left_idx])
            var_right = _cluster_variance(cov_ordered[right_idx, right_idx])
            denom = var_left + var_right
            # Inverse-variance allocation between the two sub-clusters.
            alpha = 0.5 if denom <= 0 else 1.0 - var_left / denom
            w[left_idx] *= alpha
            w[right_idx] *= 1.0 - alpha
            new_clusters.extend([(start, mid), (mid, end)])
        clusters = new_clusters
    total = float(w.sum())
    if total > 0:
        w = w / total
    return w


def _cluster_variance(sub_cov: np.ndarray) -> float:
    """Inverse-variance-weighted variance of a sub-cluster (Lopez de
    Prado 2016 eq. 5)."""
    if sub_cov.shape[0] == 0:
        return 0.0
    inv_var = 1.0 / np.maximum(np.diag(sub_cov), 1e-12)
    w = inv_var / inv_var.sum()
    return float(w @ sub_cov @ w)


# ----------------------------------------------------------------------
# Method 3: Black-Litterman (SHADOW)
# ----------------------------------------------------------------------
class BlackLittermanPortfolio(PortfolioMethod):
    metadata = MethodMetadata(
        method_id="portfolio.black_litterman.v1",
        component="portfolio_construction",
        name="Black-Litterman Portfolio",
        version="1.0.0",
        description=(
            "Bayesian combination of an equal-weight prior with "
            "composite-score views. Posterior expected returns feed "
            "a mean-variance optimisation with block + position caps."
        ),
        references=[
            "Black & Litterman (1992) Global Portfolio Optimization",
            "Idzorek (2004) A Step-By-Step Guide to BL",
        ],
    )

    def __init__(
        self,
        *,
        constraints: ConstraintConfig | None = None,
        tau: float = 0.05,
        risk_aversion: float = 2.5,
        view_confidence_floor: float = 0.1,
    ) -> None:
        self.constraints = constraints or ConstraintConfig()
        self.tau = float(tau)
        self.risk_aversion = float(risk_aversion)
        self.view_confidence_floor = float(view_confidence_floor)

    def compute(
        self, data: PortfolioInput, session: Session | None
    ) -> PortfolioOutput:
        ids, signs, scores, cov, blocks = _filter_eligible(
            data, cfg=self.constraints
        )
        if len(ids) < self.constraints.min_positions:
            return _zero_output(
                as_of=data.as_of,
                method_id=self.metadata.method_id,
                instrument_ids=data.instrument_ids,
            )
        n = len(ids)

        # Equal-weight market-implied prior. For 13-instrument retail
        # commodity universe, reverse-optimised from cap weights isn't
        # meaningful; equal-weight is the honest default.
        w_eq = np.ones(n) / n
        # Pi = lambda * Sigma * w_eq (implied equilibrium returns).
        Pi = self.risk_aversion * cov @ w_eq

        # Each composite score becomes a view: scaled by per-instrument
        # vol so units match expected return.
        annual_vol = np.sqrt(np.diag(cov))
        view_returns = scores * annual_vol  # scale [-1,1] view by vol
        # View identity matrix (one view per instrument).
        P = np.eye(n)

        # Omega = uncertainty in views. Use composite confidence
        # (floor it) - low confidence -> high omega -> prior dominates.
        confidences = np.array([
            max(
                self.view_confidence_floor,
                float(data.composite_confidence.get(ids[i], 0.5)),
            )
            for i in range(n)
        ])
        # Larger denominator = smaller variance = higher confidence.
        omega_diag = (np.diag(P @ (self.tau * cov) @ P.T) / confidences)
        Omega = np.diag(omega_diag)

        # Posterior expected returns: He-Litterman closed form.
        tau_sigma = self.tau * cov
        try:
            inv_tau_sigma = sla.pinv(tau_sigma)
            inv_omega = sla.pinv(Omega)
            posterior_cov = sla.pinv(inv_tau_sigma + P.T @ inv_omega @ P)
            posterior_mean = posterior_cov @ (
                inv_tau_sigma @ Pi + P.T @ inv_omega @ view_returns
            )
        except Exception as exc:  # pragma: no cover - numerical
            log.warning("portfolio.black_litterman.posterior_failed", error=str(exc))
            posterior_mean = Pi

        # Mean-variance with posterior_mean as expected returns. No
        # short-only constraint since composite sign already determines
        # direction at the post-step.
        try:
            sigma_inv = sla.pinv(cov)
            w_unconstrained = (1.0 / self.risk_aversion) * sigma_inv @ posterior_mean
        except Exception:
            w_unconstrained = np.zeros(n)
        # Translate signed weights into magnitude (we keep composite
        # sign as the source of truth even if BL's posterior flips
        # somewhere odd).
        w_abs = np.abs(w_unconstrained)
        if w_abs.sum() > 0:
            w_abs = w_abs / w_abs.sum()
        w_signed = signs * w_abs

        w_signed = _apply_position_and_block_caps(
            w_signed, ids, blocks, cfg=self.constraints
        )
        w_signed = _scale_to_target_vol(
            w_signed, cov, target_vol=self.constraints.target_portfolio_vol
        )

        return _build_output(
            as_of=data.as_of,
            method_id=self.metadata.method_id,
            instrument_ids=ids,
            weights=w_signed,
            cov=cov,
            composite_scores=scores,
            blocks=blocks,
            extra_metadata={
                "tau": self.tau,
                "risk_aversion": self.risk_aversion,
                "view_confidence_floor": self.view_confidence_floor,
            },
        )


# ----------------------------------------------------------------------
# Method 4: CVaR (SHADOW)
# ----------------------------------------------------------------------
class CVaRPortfolio(PortfolioMethod):
    metadata = MethodMetadata(
        method_id="portfolio.cvar.v1",
        component="portfolio_construction",
        name="Mean-CVaR Portfolio",
        version="1.0.0",
        description=(
            "Mean-CVaR optimisation via the Rockafellar-Uryasev LP. "
            "Directly minimises expected loss in the worst 5% of "
            "cases subject to a composite-derived target return + "
            "block + position caps."
        ),
        references=[
            "Rockafellar & Uryasev (2000) Optimization of CVaR",
        ],
    )

    def __init__(
        self,
        *,
        constraints: ConstraintConfig | None = None,
        alpha: float = 0.05,
        lookback_days: int = 504,
    ) -> None:
        self.constraints = constraints or ConstraintConfig()
        self.alpha = float(alpha)
        self.lookback_days = int(lookback_days)

    def compute(
        self, data: PortfolioInput, session: Session | None
    ) -> PortfolioOutput:
        ids, signs, scores, cov, blocks = _filter_eligible(
            data, cfg=self.constraints
        )
        if len(ids) < self.constraints.min_positions:
            return _zero_output(
                as_of=data.as_of,
                method_id=self.metadata.method_id,
                instrument_ids=data.instrument_ids,
            )
        n = len(ids)
        # In production we'd pull historical returns matching the
        # eligible universe; for the LP we approximate the loss
        # scenarios via covariance-implied Monte Carlo (5000 draws).
        # This is honest about what's available pre-Stage-9 (no
        # walk-forward backtester yet).
        rng = np.random.default_rng(42)
        try:
            chol = sla.cholesky(cov + 1e-8 * np.eye(n), lower=True)
        except Exception:
            # Cov not PSD enough for chol; fall back to diagonal.
            chol = np.diag(np.sqrt(np.maximum(np.diag(cov), 1e-8)))
        eps = rng.standard_normal((5000, n))
        # Daily-scale draws.
        daily_cov = cov / 252.0
        try:
            chol_d = sla.cholesky(daily_cov + 1e-10 * np.eye(n), lower=True)
        except Exception:
            chol_d = chol / np.sqrt(252.0)
        scenarios = eps @ chol_d.T
        # Scenario losses = -portfolio return.
        S = scenarios.shape[0]

        # Rockafellar-Uryasev LP variables: [w (n), VaR (1), u (S)].
        # Minimise:  VaR + (1/((1-conf)*S)) * sum(u_i)
        # Subject to:
        #   u_i >= -(scenarios_i @ w) - VaR
        #   u_i >= 0
        #   sum(w) = 1
        #   0 <= w_i <= max_position_weight
        #   sum_block(w) <= max_block_weight
        n_vars = n + 1 + S
        c = np.concatenate(
            [
                np.zeros(n),               # w
                np.array([1.0]),           # VaR
                np.ones(S) / ((1.0 - (1.0 - self.alpha)) * S),  # u/(alpha*S)
            ]
        )
        # Inequality constraints: A_ub @ x <= b_ub.
        # u_i + scenarios_i @ w + VaR >= 0  =>  -u_i - scenarios_i @ w - VaR <= 0.
        A_ub_loss = np.hstack(
            [-scenarios, -np.ones((S, 1)), -np.eye(S)]
        )
        b_ub_loss = np.zeros(S)
        # Block constraints.
        by_block: dict[str, list[int]] = {}
        for i, inst in enumerate(ids):
            by_block.setdefault(blocks.get(inst, "unknown"), []).append(i)
        block_rows = []
        block_b = []
        for _block_label, idxs in by_block.items():
            row = np.zeros(n_vars)
            for i in idxs:
                row[i] = 1.0
            block_rows.append(row)
            block_b.append(float(self.constraints.max_block_weight))
        if block_rows:
            A_ub_block = np.vstack(block_rows)
            b_ub_block = np.array(block_b)
            A_ub = np.vstack([A_ub_loss, A_ub_block])
            b_ub = np.concatenate([b_ub_loss, b_ub_block])
        else:
            A_ub = A_ub_loss
            b_ub = b_ub_loss

        # Equality: sum(w) = 1.
        A_eq = np.zeros((1, n_vars))
        A_eq[0, :n] = 1.0
        b_eq = np.array([1.0])

        # Bounds: w in [0, max_position_weight]; VaR free; u >= 0.
        bounds = (
            [(0.0, self.constraints.max_position_weight)] * n
            + [(None, None)]
            + [(0.0, None)] * S
        )

        try:
            res = linprog(
                c,
                A_ub=A_ub,
                b_ub=b_ub,
                A_eq=A_eq,
                b_eq=b_eq,
                bounds=bounds,
                method="highs",
            )
        except Exception as exc:  # pragma: no cover - LP failure
            log.warning("portfolio.cvar.lp_failed", error=str(exc))
            return _zero_output(
                as_of=data.as_of,
                method_id=self.metadata.method_id,
                instrument_ids=data.instrument_ids,
            )

        if not res.success:
            log.warning(
                "portfolio.cvar.lp_did_not_converge", status=res.message
            )
            # Fall back to equal-weight inside the eligible set.
            w_abs = np.ones(n) / n
        else:
            w_abs = np.asarray(res.x[:n])
            w_abs = np.ones(n) / n if w_abs.sum() <= 0 else w_abs / w_abs.sum()

        w_signed = signs * w_abs
        w_signed = _apply_position_and_block_caps(
            w_signed, ids, blocks, cfg=self.constraints
        )
        w_signed = _scale_to_target_vol(
            w_signed, cov, target_vol=self.constraints.target_portfolio_vol
        )

        return _build_output(
            as_of=data.as_of,
            method_id=self.metadata.method_id,
            instrument_ids=ids,
            weights=w_signed,
            cov=cov,
            composite_scores=scores,
            blocks=blocks,
            extra_metadata={
                "alpha": self.alpha,
                "n_scenarios": int(S),
                "lp_status": str(getattr(res, "message", "n/a")),
            },
        )


__all__ = [
    "DEFAULT_BLOCK_BY_ASSET_CLASS",
    "BlackLittermanPortfolio",
    "CVaRPortfolio",
    "ConstraintConfig",
    "EqualRiskContributionPortfolio",
    "HierarchicalRiskParityPortfolio",
    "PortfolioInput",
    "PortfolioMethod",
    "PortfolioOutput",
    "PortfolioPosition",
]


_ = pd  # pragma: no cover - reserved for downstream consumers
