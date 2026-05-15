"""Positioning-family method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.signals.positioning.methods import CotCommercial, CotZScore

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def register(session: Session) -> None:
    """Register the two positioning methods. Idempotent."""
    register_method(
        CotZScore(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 4A — managed-money net positioning z-score (disaggregated)",
    )
    register_method(
        CotCommercial(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 4A — commercial net positioning extremes (legacy)",
    )
