"""Integration tests: registry + DB persistence + promotion gate."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from macro_trader.db.models.system import (
    MethodComparisonRow,
    MethodRegistryRow,
    MethodStatusHistoryRow,
)
from macro_trader.methods.base import Method, MethodMetadata
from macro_trader.methods.comparator import MethodComparator
from macro_trader.methods.promotion import PromotionCriteria, evaluate_promotion
from macro_trader.methods.registry import MethodRegistry
from macro_trader.methods.status import MethodStatus


# ----- Test doubles (mirrors tests.unit.test_methods_framework) -----
class _Identity(Method[np.ndarray, np.ndarray]):
    def __init__(self, method_id: str = "int.identity.v1") -> None:
        self.metadata = MethodMetadata(
            method_id=method_id,
            component="int_component",
            name="Identity",
            version="1.0.0",
            description="identity",
            references=[],
        )

    def fit(self, data: np.ndarray) -> None:
        pass

    def predict(self, data: np.ndarray) -> np.ndarray:
        return np.asarray(data)

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> _Identity:
        return cls()


class _NoisyIdentity(_Identity):
    def __init__(self, method_id: str = "int.noisy.v1") -> None:
        super().__init__(method_id=method_id)
        self.metadata = MethodMetadata(
            method_id=method_id,
            component="int_component",
            name="NoisyIdentity",
            version="1.0.0",
            description="identity+noise",
            references=[],
        )

    def predict(self, data: np.ndarray) -> np.ndarray:
        return np.asarray(data) + 0.001


class _Comparator(MethodComparator[np.ndarray, np.ndarray]):
    def __init__(self) -> None:
        super().__init__(component="int_component")

    def _compute_metrics(self, output_a, output_b, *, data):  # type: ignore[no-untyped-def]
        return {
            "sharpe_a": 1.0,
            "sharpe_b": 1.2,
            "stability_a": 0.9,
            "stability_b": 0.95,
        }


@pytest.mark.integration
def test_register_method_persists_to_db(db_session) -> None:
    reg = MethodRegistry()
    m = _Identity()
    reg.register(m, MethodStatus.BASELINE, session=db_session, reason="seed")
    db_session.flush()
    row = db_session.get(MethodRegistryRow, "int.identity.v1")
    assert row is not None
    assert row.status == MethodStatus.BASELINE
    assert row.component == "int_component"

    # And a history row.
    history = db_session.query(MethodStatusHistoryRow).filter_by(method_id="int.identity.v1").all()
    assert len(history) == 1
    assert history[0].new_status == MethodStatus.BASELINE
    assert history[0].old_status is None


@pytest.mark.integration
def test_set_status_appends_history(db_session) -> None:
    reg = MethodRegistry()
    m = _NoisyIdentity()
    reg.register(m, MethodStatus.DEVELOPMENT, session=db_session, reason="seed")
    reg.set_status("int.noisy.v1", MethodStatus.SHADOW, reason="ready", session=db_session)
    db_session.flush()
    history = (
        db_session.query(MethodStatusHistoryRow)
        .filter_by(method_id="int.noisy.v1")
        .order_by(MethodStatusHistoryRow.changed_at)
        .all()
    )
    assert len(history) == 2
    assert history[1].old_status == MethodStatus.DEVELOPMENT
    assert history[1].new_status == MethodStatus.SHADOW


@pytest.mark.integration
def test_comparator_persists_result(db_session) -> None:
    a = _Identity()
    b = _NoisyIdentity()
    c = _Comparator()
    result = c.compare(
        a,
        b,
        np.linspace(-1, 1, 16),
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 2, 1, tzinfo=UTC),
        notes="t",
        session=db_session,
    )
    db_session.flush()
    row = db_session.get(MethodComparisonRow, result.comparison_id)
    assert row is not None
    assert row.method_a_id == "int.identity.v1"
    assert row.method_b_id == "int.noisy.v1"


@pytest.mark.integration
def test_promotion_gate_rejects_when_no_comparisons(db_session) -> None:
    reg = MethodRegistry()
    reg.register(_NoisyIdentity(), MethodStatus.SHADOW, session=db_session, reason="x")
    db_session.flush()
    eligible, evidence = evaluate_promotion(
        "int.noisy.v1",
        PromotionCriteria(
            component="int_component",
            min_shadow_period_days=0,
            min_comparison_runs=1,
            required_improvements=["sharpe"],
            improvement_threshold=0.0,
        ),
        session=db_session,
    )
    assert eligible is False
    assert evidence["checks"]["comparison_runs"]["pass"] is False


@pytest.mark.integration
def test_promotion_gate_eligible_when_criteria_met(db_session) -> None:
    reg = MethodRegistry()
    reg.register(_Identity(), MethodStatus.BASELINE, session=db_session, reason="x")
    reg.register(_NoisyIdentity(), MethodStatus.SHADOW, session=db_session, reason="x")
    c = _Comparator()
    for i in range(3):
        c.compare(
            _Identity(),
            _NoisyIdentity(),
            np.linspace(-1, 1, 8),
            period_start=datetime(2026, 1, i + 1, tzinfo=UTC),
            period_end=datetime(2026, 1, i + 2, tzinfo=UTC),
            session=db_session,
        )
    db_session.flush()
    eligible, evidence = evaluate_promotion(
        "int.noisy.v1",
        PromotionCriteria(
            component="int_component",
            min_shadow_period_days=0,
            min_comparison_runs=2,
            required_improvements=["sharpe", "stability"],
            improvement_threshold=0.01,
        ),
        session=db_session,
    )
    assert eligible is True, evidence
    assert all(v["pass"] for v in evidence["checks"]["required_improvements"].values())
