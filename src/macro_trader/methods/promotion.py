"""Promotion gate logic.

A method is only *eligible* for promotion when:

1. It has been in ``SHADOW`` for at least ``min_shadow_period_days`` days.
2. There are at least ``min_comparison_runs`` recorded comparisons against
   the current production/baseline.
3. On every metric in ``required_improvements`` the shadow has out-performed
   the baseline by at least ``improvement_threshold`` (relative).

Eligibility is *necessary but not sufficient* for promotion — the actual
promotion is always a manual human decision invoked through the API or CLI.
This module never auto-promotes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from macro_trader.methods.status import MethodStatus
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(slots=True)
class PromotionCriteria:
    """The bar a shadow method must clear to be eligible for promotion."""

    component: str
    min_shadow_period_days: int = 180
    min_comparison_runs: int = 12
    required_improvements: list[str] = field(default_factory=list)
    """Metric keys (from ``ComparisonResult.metrics``) on which the shadow
    must out-perform the baseline. Example: ``["sharpe", "max_dd_ratio",
    "stability"]``. The convention is that each metric is recorded twice with
    ``_a`` and ``_b`` suffixes (a = baseline, b = shadow); the relative
    improvement is ``(b - a) / |a|``."""
    improvement_threshold: float = 0.05
    """Minimum relative improvement on each required metric (default 5%)."""

    def __post_init__(self) -> None:
        if self.min_shadow_period_days < 0:
            raise ValueError("min_shadow_period_days must be non-negative")
        if self.min_comparison_runs < 0:
            raise ValueError("min_comparison_runs must be non-negative")
        if not -1.0 < self.improvement_threshold:
            raise ValueError("improvement_threshold must be > -1.0")


def evaluate_promotion(
    method_id: str,
    criteria: PromotionCriteria,
    *,
    session: Session,
) -> tuple[bool, dict[str, Any]]:
    """Compute eligibility for promotion.

    Returns ``(eligible, evidence)`` where ``evidence`` is a structured dict
    explaining which criteria passed / failed. This function NEVER mutates
    state; it only reads from the DB.
    """
    from macro_trader.db.models.system import MethodComparisonRow, MethodRegistryRow

    evidence: dict[str, Any] = {
        "method_id": method_id,
        "component": criteria.component,
        "criteria": {
            "min_shadow_period_days": criteria.min_shadow_period_days,
            "min_comparison_runs": criteria.min_comparison_runs,
            "required_improvements": list(criteria.required_improvements),
            "improvement_threshold": criteria.improvement_threshold,
        },
        "checks": {},
    }

    # ----- Method must exist and be in SHADOW status. -----
    row = session.get(MethodRegistryRow, method_id)
    if row is None:
        evidence["checks"]["exists"] = {"pass": False, "detail": "method not found"}
        return False, evidence
    evidence["checks"]["exists"] = {"pass": True}

    if row.status != MethodStatus.SHADOW:
        evidence["checks"]["status"] = {
            "pass": False,
            "detail": f"status is {row.status}, expected {MethodStatus.SHADOW}",
        }
        return False, evidence
    evidence["checks"]["status"] = {"pass": True}

    # ----- Shadow period. -----
    shadow_age = utcnow() - row.status_changed_at
    days = shadow_age / timedelta(days=1)
    period_ok = days >= criteria.min_shadow_period_days
    evidence["checks"]["shadow_period"] = {
        "pass": period_ok,
        "days": float(days),
        "required": criteria.min_shadow_period_days,
    }

    # ----- Comparison runs. -----
    from sqlalchemy import select

    stmt = select(MethodComparisonRow).where(
        MethodComparisonRow.component == criteria.component,
        MethodComparisonRow.method_b_id == method_id,
    )
    comparisons = list(session.scalars(stmt))
    runs_ok = len(comparisons) >= criteria.min_comparison_runs
    evidence["checks"]["comparison_runs"] = {
        "pass": runs_ok,
        "count": len(comparisons),
        "required": criteria.min_comparison_runs,
    }

    # ----- Required-metric improvements. -----
    metric_results: dict[str, dict[str, float | bool]] = {}
    all_metrics_ok = True
    for metric in criteria.required_improvements:
        a_key = f"{metric}_a"
        b_key = f"{metric}_b"
        deltas: list[float] = []
        for c in comparisons:
            if a_key in c.metrics and b_key in c.metrics:
                a_val = c.metrics[a_key]
                b_val = c.metrics[b_key]
                if a_val == 0:
                    continue
                deltas.append((b_val - a_val) / abs(a_val))
        if not deltas:
            metric_results[metric] = {"pass": False, "detail": 0.0, "n_samples": 0}
            all_metrics_ok = False
            continue
        mean_delta = sum(deltas) / len(deltas)
        passed = mean_delta >= criteria.improvement_threshold
        metric_results[metric] = {
            "pass": passed,
            "mean_relative_improvement": float(mean_delta),
            "n_samples": len(deltas),
            "threshold": criteria.improvement_threshold,
        }
        if not passed:
            all_metrics_ok = False
    evidence["checks"]["required_improvements"] = metric_results

    eligible = bool(period_ok and runs_ok and all_metrics_ok)
    evidence["eligible"] = eligible
    return eligible, evidence
