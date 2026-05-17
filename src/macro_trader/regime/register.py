"""Regime-classifier method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.regime.methods import (
    BOCPDRegimeClassifier,
    GMMRegimeClassifier,
    MSVARRegimeClassifier,
    RulesRegimeClassifier,
    _hmmlearn_available,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def register(session: Session) -> None:
    register_method(
        RulesRegimeClassifier(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 6 - rules-based regime classifier baseline",
    )
    register_method(
        GMMRegimeClassifier(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 6 - GMM regime shadow",
    )
    register_method(
        BOCPDRegimeClassifier(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 6 - BOCPD changepoint shadow",
    )
    # HMM gated on hmmlearn (core dep; check anyway for prod images
    # that strip optional libs).
    if _hmmlearn_available():
        from macro_trader.regime.methods import HMMRegimeClassifier

        register_method(
            HMMRegimeClassifier(),
            MethodStatus.SHADOW,
            session=session,
            reason="Stage 6 - HMM regime shadow",
        )
    else:
        log.info(
            "methods.setup.skipped",
            method_id="regime.hmm.v1",
            reason="hmmlearn not installed",
        )
    # MS-VAR: register only if statsmodels MarkovRegression is
    # importable.
    try:
        register_method(
            MSVARRegimeClassifier(),
            MethodStatus.SHADOW,
            session=session,
            reason="Stage 6 - MS-VAR regime shadow (univariate fallback)",
        )
    except Exception as exc:  # pragma: no cover - statsmodels env
        log.warning(
            "methods.setup.skipped",
            method_id="regime.msvar.v1",
            error=str(exc),
        )
