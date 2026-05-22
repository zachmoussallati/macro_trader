"""Portfolio construction method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.portfolio.construction.methods import (
    BlackLittermanPortfolio,
    CVaRPortfolio,
    EqualRiskContributionPortfolio,
    HierarchicalRiskParityPortfolio,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def register(session: Session) -> None:
    register_method(
        EqualRiskContributionPortfolio(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 8 - ERC baseline",
    )
    register_method(
        HierarchicalRiskParityPortfolio(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 8 - HRP shadow",
    )
    register_method(
        BlackLittermanPortfolio(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 8 - Black-Litterman shadow",
    )
    register_method(
        CVaRPortfolio(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 8 - Mean-CVaR shadow",
    )
