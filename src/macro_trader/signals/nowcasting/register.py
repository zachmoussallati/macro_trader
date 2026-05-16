"""Nowcasting family registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.signals.nowcasting.methods import BVARNowcaster, OLSARNowcaster

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def register(session: Session) -> None:
    register_method(
        OLSARNowcaster(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 5 - OLS-AR nowcasting baseline",
    )
    register_method(
        BVARNowcaster(),
        MethodStatus.SHADOW,
        session=session,
        reason=(
            "Stage 5 - Bayesian-prior nowcasting shadow "
            "(univariate Minnesota-flavoured prior)"
        ),
    )
