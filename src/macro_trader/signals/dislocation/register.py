"""Dislocation-family method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.signals.dislocation.methods import (
    DynamicFactorModel,
    PCADislocation,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def register(session: Session) -> None:
    """Register the two dislocation methods. Idempotent."""
    register_method(
        PCADislocation(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 4A — PCA residual-based dislocation",
    )
    register_method(
        DynamicFactorModel(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 4A — Dynamic Factor Model with time-varying loadings",
    )
