"""Method comparison framework.

A ``MethodComparator`` takes two ``Method`` instances (typically the
production/baseline and a shadow) and a common input, runs both on identical
data, and produces a ``ComparisonResult`` capturing component-specific metrics
plus generic agreement and stability diagnostics.

Each pipeline component (factor exposure, dislocation, regime, signal
combination, portfolio construction, ...) will subclass ``MethodComparator``
and define its own ``_metrics`` body. The base class wires up the lifecycle
and persists the result to ``system.method_comparisons``.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Generic

from macro_trader.methods.base import InputT, Method, OutputT
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(slots=True)
class ComparisonResult:
    """The structured output of a single comparison run."""

    comparison_id: str
    component: str
    method_a_id: str
    """Typically the baseline / production method (the reference)."""
    method_b_id: str
    """Typically the shadow / enhancement method (the candidate)."""
    period_start: datetime
    period_end: datetime
    metrics: dict[str, float] = field(default_factory=dict)
    """Component-specific metrics. Convention: keys that end in ``_a`` or
    ``_b`` are the metric computed for each method; keys without a suffix are
    deltas (b minus a) or composite scores."""
    agreement: dict[str, float] = field(default_factory=dict)
    """How closely the two methods' outputs match (correlation, rank-corr,
    exact-match rate, etc.). Filled by :func:`macro_trader.methods.metrics`."""
    stability: dict[str, float] = field(default_factory=dict)
    """How stable each method's output is over time (rolling correlation,
    parameter drift, etc.). Filled by :func:`macro_trader.methods.metrics`."""
    notes: str = ""
    created_at: datetime = field(default_factory=utcnow)

    @classmethod
    def new(
        cls,
        *,
        component: str,
        method_a_id: str,
        method_b_id: str,
        period_start: datetime,
        period_end: datetime,
        notes: str = "",
    ) -> ComparisonResult:
        return cls(
            comparison_id=str(uuid.uuid4()),
            component=component,
            method_a_id=method_a_id,
            method_b_id=method_b_id,
            period_start=period_start,
            period_end=period_end,
            notes=notes,
        )

    def to_db_row(self) -> dict[str, Any]:
        """Serialise to the dict shape consumed by `MethodComparisonRow`."""
        return {
            "comparison_id": self.comparison_id,
            "component": self.component,
            "method_a_id": self.method_a_id,
            "method_b_id": self.method_b_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "metrics": dict(self.metrics),
            "agreement": dict(self.agreement),
            "stability": dict(self.stability),
            "notes": self.notes,
            "created_at": self.created_at,
        }


class MethodComparator(ABC, Generic[InputT, OutputT]):
    """Abstract base class for component-specific comparators.

    Concrete subclasses MUST override :meth:`_compute_metrics`, which receives
    the two methods' outputs and returns a flat ``dict[str, float]`` of
    component-specific metrics. Generic agreement and stability diagnostics
    are filled by :meth:`compare` using :mod:`macro_trader.methods.metrics`.
    """

    component: str
    """The component this comparator handles (``"regime_classifier"`` etc.)."""

    def __init__(self, component: str) -> None:
        self.component = component

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------
    @abstractmethod
    def _compute_metrics(
        self,
        output_a: OutputT,
        output_b: OutputT,
        *,
        data: InputT,
    ) -> dict[str, float]:
        """Component-specific metrics (e.g. Sharpe of resulting signal, hit
        rate, calibration error, drawdown, etc.). Subclasses override this."""

    def _compute_agreement(self, output_a: OutputT, output_b: OutputT) -> dict[str, float]:
        """Default agreement: correlation + exact-match rate if applicable."""
        from macro_trader.methods.metrics import output_agreement

        return output_agreement(output_a, output_b)

    def _compute_stability(
        self,
        method_a: Method[InputT, OutputT],
        method_b: Method[InputT, OutputT],
        data: InputT,
    ) -> dict[str, float]:
        """Default stability diagnostics. Subclasses can override for
        component-specific behaviour (e.g. parameter drift for regression)."""
        from macro_trader.methods.metrics import output_stability

        return output_stability(method_a, method_b, data)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def compare(
        self,
        method_a: Method[InputT, OutputT],
        method_b: Method[InputT, OutputT],
        data: InputT,
        period_start: datetime,
        period_end: datetime,
        *,
        notes: str = "",
        session: Session | None = None,
    ) -> ComparisonResult:
        """Run both methods on `data` and return a `ComparisonResult`."""
        if method_a.metadata.component != self.component:
            raise ValueError(
                f"method_a component {method_a.metadata.component!r} != "
                f"comparator component {self.component!r}"
            )
        if method_b.metadata.component != self.component:
            raise ValueError(
                f"method_b component {method_b.metadata.component!r} != "
                f"comparator component {self.component!r}"
            )

        output_a = method_a.predict(data)
        output_b = method_b.predict(data)

        result = ComparisonResult.new(
            component=self.component,
            method_a_id=method_a.metadata.method_id,
            method_b_id=method_b.metadata.method_id,
            period_start=period_start,
            period_end=period_end,
            notes=notes,
        )
        result.metrics = self._compute_metrics(output_a, output_b, data=data)
        result.agreement = self._compute_agreement(output_a, output_b)
        result.stability = self._compute_stability(method_a, method_b, data)

        if session is not None:
            self._persist(result, session)

        return result

    def _persist(self, result: ComparisonResult, session: Session) -> None:
        from macro_trader.db.models.system import MethodComparisonRow

        row = MethodComparisonRow(**result.to_db_row())
        session.add(row)
        session.flush()
