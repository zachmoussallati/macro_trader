"""Vol-surface family registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.signals.vol_surface.methods import RawVolSurface, SVIVolSurface

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def register(session: Session) -> None:
    """Both methods register but cannot reach PRODUCTION promotion
    until the historical-data limitation (yfinance only) is resolved
    by a paid options-data source post-v1."""
    register_method(
        RawVolSurface(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 5 - raw chain-quote surface metrics",
    )
    register_method(
        SVIVolSurface(),
        MethodStatus.SHADOW,
        session=session,
        reason=(
            "Stage 5 - spline-fitted surface with calendar arbitrage check "
            "(fallback for full Gatheral SVI)"
        ),
    )
