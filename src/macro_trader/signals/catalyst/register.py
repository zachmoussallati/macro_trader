"""Catalyst-family method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.signals.catalyst.methods import EventStudyCatalyst, _econml_available

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def register(session: Session) -> None:
    register_method(
        EventStudyCatalyst(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 4B - event-study catalyst sensitivity baseline",
    )
    if _econml_available():
        from macro_trader.signals.catalyst.methods import CausalCatalyst

        register_method(
            CausalCatalyst(),
            MethodStatus.SHADOW,
            session=session,
            reason="Stage 4B - causal-inference catalyst sensitivity shadow",
        )
    else:
        log.info(
            "methods.setup.skipped",
            method_id="catalyst.causal.v1",
            reason="EconML not installed; install via `uv sync --extra ml`",
        )
