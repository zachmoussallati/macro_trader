"""Composite-method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.composite.methods import (
    BayesianHierarchicalComposite,
    LinearComposite,
    _lightgbm_available,
)
from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def register(session: Session) -> None:
    register_method(
        LinearComposite(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 7 - linear composite baseline",
    )
    register_method(
        BayesianHierarchicalComposite(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 7 - bayesian hierarchical composite shadow",
    )
    if _lightgbm_available():
        from macro_trader.composite.methods import GBMComposite

        register_method(
            GBMComposite(),
            MethodStatus.SHADOW,
            session=session,
            reason="Stage 7 - GBM composite shadow",
        )
    else:
        log.info(
            "methods.setup.skipped",
            method_id="composite.gbm.v1",
            reason="lightgbm not installed",
        )
