"""Unit tests for the methods framework.

Uses two trivial test methods — ``IdentityMethod`` and ``NoisyIdentityMethod``
— and a small ``IdentityComparator`` so we can exercise the registry, status
transitions, comparator wiring, and promotion gate without depending on the
database.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pytest

from macro_trader.methods.base import Method, MethodMetadata
from macro_trader.methods.comparator import ComparisonResult, MethodComparator
from macro_trader.methods.metrics import output_agreement, output_stability
from macro_trader.methods.promotion import PromotionCriteria
from macro_trader.methods.registry import (
    MethodAlreadyRegisteredError,
    MethodNotFoundError,
    MethodRegistry,
    get_default_registry,
)
from macro_trader.methods.status import MethodStatus


# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------
@dataclass
class _State:
    scale: float = 1.0


class IdentityMethod(Method[np.ndarray, np.ndarray]):
    """The simplest baseline: returns the input unchanged."""

    def __init__(self, method_id: str = "trivial.identity.v1") -> None:
        self.metadata = MethodMetadata(
            method_id=method_id,
            component="trivial_component",
            name="Identity",
            version="1.0.0",
            description="Returns the input unchanged.",
            references=[],
        )
        self._state = _State()

    def fit(self, data: np.ndarray) -> None:
        self._state.scale = 1.0  # nothing to learn

    def predict(self, data: np.ndarray) -> np.ndarray:
        return np.asarray(data) * self._state.scale

    def serialize(self) -> bytes:
        return pickle.dumps(self._state)

    @classmethod
    def deserialize(cls, blob: bytes) -> IdentityMethod:
        m = cls()
        m._state = pickle.loads(blob)
        return m


class NoisyIdentityMethod(Method[np.ndarray, np.ndarray]):
    """A 'shadow' that adds a tiny deterministic offset."""

    def __init__(self, method_id: str = "trivial.noisy.v1", noise: float = 0.01) -> None:
        self.metadata = MethodMetadata(
            method_id=method_id,
            component="trivial_component",
            name="NoisyIdentity",
            version="1.0.0",
            description="Identity + fixed small offset.",
            references=[],
        )
        self._noise = noise

    def fit(self, data: np.ndarray) -> None:
        pass

    def predict(self, data: np.ndarray) -> np.ndarray:
        return np.asarray(data) + self._noise

    def serialize(self) -> bytes:
        return pickle.dumps(self._noise)

    @classmethod
    def deserialize(cls, blob: bytes) -> NoisyIdentityMethod:
        return cls(noise=pickle.loads(blob))


class IdentityComparator(MethodComparator[np.ndarray, np.ndarray]):
    def __init__(self) -> None:
        super().__init__(component="trivial_component")

    def _compute_metrics(
        self, output_a: np.ndarray, output_b: np.ndarray, *, data: np.ndarray
    ) -> dict[str, float]:
        mse_a = float(np.mean((output_a - data) ** 2))
        mse_b = float(np.mean((output_b - data) ** 2))
        return {
            "mse_a": mse_a,
            "mse_b": mse_b,
            "mse_delta": mse_b - mse_a,
        }


# ----------------------------------------------------------------------
# Registry tests
# ----------------------------------------------------------------------
@pytest.mark.unit
def test_register_and_fetch() -> None:
    reg = MethodRegistry()
    m = IdentityMethod()
    reg.register(m, MethodStatus.BASELINE)
    assert reg.get("trivial.identity.v1") is m
    assert reg.status_of("trivial.identity.v1") == MethodStatus.BASELINE


@pytest.mark.unit
def test_register_idempotent_same_metadata() -> None:
    reg = MethodRegistry()
    m = IdentityMethod()
    reg.register(m, MethodStatus.BASELINE)
    reg.register(m, MethodStatus.BASELINE)  # second time is a no-op
    assert len(reg.list()) == 1


@pytest.mark.unit
def test_register_rejects_metadata_mismatch() -> None:
    reg = MethodRegistry()
    m1 = IdentityMethod()
    reg.register(m1, MethodStatus.BASELINE)

    # Same id, different version.
    m2 = IdentityMethod()
    m2.metadata = MethodMetadata(
        method_id="trivial.identity.v1",
        component="trivial_component",
        name="Identity",
        version="2.0.0",
        description="different version",
        references=[],
    )
    with pytest.raises(MethodAlreadyRegisteredError):
        reg.register(m2, MethodStatus.BASELINE)


@pytest.mark.unit
def test_list_filters_by_component_and_status() -> None:
    reg = MethodRegistry()
    reg.register(IdentityMethod(), MethodStatus.BASELINE)
    reg.register(NoisyIdentityMethod(), MethodStatus.SHADOW)
    base = reg.list(status=MethodStatus.BASELINE)
    shadow = reg.list(status=MethodStatus.SHADOW)
    assert len(base) == 1 and base[0].method_id == "trivial.identity.v1"
    assert len(shadow) == 1 and shadow[0].method_id == "trivial.noisy.v1"
    comp = reg.list(component="trivial_component")
    assert len(comp) == 2


@pytest.mark.unit
def test_status_transitions() -> None:
    reg = MethodRegistry()
    reg.register(NoisyIdentityMethod(), MethodStatus.DEVELOPMENT)
    reg.set_status("trivial.noisy.v1", MethodStatus.SHADOW, reason="ready")
    assert reg.status_of("trivial.noisy.v1") == MethodStatus.SHADOW


@pytest.mark.unit
def test_set_status_unknown_method_raises() -> None:
    reg = MethodRegistry()
    with pytest.raises(MethodNotFoundError):
        reg.set_status("missing.id", MethodStatus.SHADOW, reason="x")


@pytest.mark.unit
def test_production_for_prefers_promoted_over_baseline() -> None:
    reg = MethodRegistry()
    reg.register(IdentityMethod(), MethodStatus.BASELINE)
    reg.register(NoisyIdentityMethod(), MethodStatus.PRODUCTION)
    assert reg.production_for("trivial_component").metadata.method_id == "trivial.noisy.v1"


@pytest.mark.unit
def test_production_for_falls_back_to_baseline() -> None:
    reg = MethodRegistry()
    reg.register(IdentityMethod(), MethodStatus.BASELINE)
    assert reg.production_for("trivial_component").metadata.method_id == "trivial.identity.v1"


@pytest.mark.unit
def test_production_for_raises_when_missing() -> None:
    reg = MethodRegistry()
    with pytest.raises(MethodNotFoundError):
        reg.production_for("nope")


@pytest.mark.unit
def test_module_level_singleton_is_a_registry() -> None:
    assert isinstance(get_default_registry(), MethodRegistry)


@pytest.mark.unit
def test_promoting_demotes_existing_production() -> None:
    reg = MethodRegistry()
    a = IdentityMethod(method_id="trivial.a")
    b = NoisyIdentityMethod(method_id="trivial.b")
    a.metadata = MethodMetadata("trivial.a", "trivial_component", "A", "1", "")
    b.metadata = MethodMetadata("trivial.b", "trivial_component", "B", "1", "")
    reg.register(a, MethodStatus.PRODUCTION)
    reg.register(b, MethodStatus.SHADOW)
    reg.set_status("trivial.b", MethodStatus.PRODUCTION, reason="promote b")
    # `a` should have been auto-demoted to DEPRECATED.
    assert reg.status_of("trivial.a") == MethodStatus.DEPRECATED
    assert reg.status_of("trivial.b") == MethodStatus.PRODUCTION


# ----------------------------------------------------------------------
# Comparator tests
# ----------------------------------------------------------------------
@pytest.mark.unit
def test_identity_comparator_runs_and_returns_valid_result() -> None:
    a = IdentityMethod()
    b = NoisyIdentityMethod()
    a.fit(np.zeros(1))
    b.fit(np.zeros(1))
    comparator = IdentityComparator()
    data = np.linspace(-1, 1, 32)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 2, 1, tzinfo=UTC)
    result = comparator.compare(a, b, data, start, end, notes="smoke")
    assert isinstance(result, ComparisonResult)
    assert result.method_a_id == "trivial.identity.v1"
    assert result.method_b_id == "trivial.noisy.v1"
    assert "mse_a" in result.metrics and "mse_b" in result.metrics
    assert result.agreement.get("pearson_correlation", 0) > 0.9


@pytest.mark.unit
def test_comparator_rejects_wrong_component() -> None:
    comparator = IdentityComparator()
    m = IdentityMethod()
    m.metadata = MethodMetadata("x", "other_component", "X", "1", "")
    with pytest.raises(ValueError):
        comparator.compare(
            m, m, np.zeros(4), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
        )


# ----------------------------------------------------------------------
# Metrics helpers
# ----------------------------------------------------------------------
@pytest.mark.unit
def test_output_agreement_for_numeric() -> None:
    a = np.array([1.0, 2.0, 3.0, 4.0])
    b = np.array([1.1, 2.1, 3.1, 4.1])
    out = output_agreement(a, b)
    assert "pearson_correlation" in out
    assert out["mae"] == pytest.approx(0.1)


@pytest.mark.unit
def test_output_agreement_for_categorical() -> None:
    a = np.array([0, 1, 1, 0])
    b = np.array([0, 1, 0, 0])
    out = output_agreement(a, b)
    assert out["exact_match_rate"] == pytest.approx(0.75)


@pytest.mark.unit
def test_output_stability_returns_perturbation_drift() -> None:
    a = IdentityMethod()
    b = NoisyIdentityMethod()
    out = output_stability(a, b, np.linspace(-1, 1, 16))
    assert "mean_perturbation_drift_a" in out
    assert "mean_perturbation_drift_b" in out
    # Identity should be perfectly stable: drift dominated by the perturbation magnitude.
    assert out["mean_perturbation_drift_a"] < 1e-3


# ----------------------------------------------------------------------
# Promotion criteria construction
# ----------------------------------------------------------------------
@pytest.mark.unit
def test_promotion_criteria_validates_negative_periods() -> None:
    with pytest.raises(ValueError):
        PromotionCriteria(component="x", min_shadow_period_days=-1)
    with pytest.raises(ValueError):
        PromotionCriteria(component="x", min_comparison_runs=-1)
    with pytest.raises(ValueError):
        PromotionCriteria(component="x", improvement_threshold=-2.0)


@pytest.mark.unit
def test_promotion_criteria_defaults() -> None:
    c = PromotionCriteria(component="x")
    assert c.min_shadow_period_days == 180
    assert c.min_comparison_runs == 12
    assert c.improvement_threshold == 0.05
    assert c.required_improvements == []
