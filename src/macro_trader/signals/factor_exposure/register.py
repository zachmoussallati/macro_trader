"""Factor exposure family method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.signals.factor_exposure.methods import (
    OLSFactorExposure,
    RandomForestFactorExposure,
    _econml_available,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def register(session: Session) -> None:
    """Register the factor exposure methods. Idempotent.

    The Causal Forest method is registered only when the optional
    ``[ml]`` extra (EconML) is installed; otherwise it's logged as
    skipped so operators can see it's missing in the methods page.
    """
    register_method(
        OLSFactorExposure(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 4B - rolling OLS factor exposure baseline",
    )
    register_method(
        RandomForestFactorExposure(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 4B - non-linear RF factor exposure shadow",
    )
    if _econml_available():
        from macro_trader.signals.factor_exposure.methods import (
            CausalForestFactorExposure,
        )

        register_method(
            CausalForestFactorExposure(),
            MethodStatus.SHADOW,
            session=session,
            reason="Stage 4B - causal forest factor exposure shadow",
        )
    else:
        log.info(
            "methods.setup.skipped",
            method_id="factor_exposure.causal_forest.v1",
            reason="EconML not installed; install via `uv sync --extra ml`",
        )
