"""Covariance method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.portfolio.covariance.methods import (
    LedoitWolfCovariance,
    _arch_available,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def register(session: Session) -> None:
    register_method(
        LedoitWolfCovariance(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 8 - Ledoit-Wolf shrinkage baseline",
    )
    if _arch_available():
        from macro_trader.portfolio.covariance.methods import DCCGARCHCovariance

        register_method(
            DCCGARCHCovariance(),
            MethodStatus.SHADOW,
            session=session,
            reason="Stage 8 - DCC-GARCH shadow",
        )
    else:
        log.info(
            "methods.setup.skipped",
            method_id="covariance.dcc_garch.v1",
            reason="arch not installed",
        )
