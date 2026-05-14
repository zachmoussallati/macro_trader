"""Carry-family method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.signals.carry.methods import CarrySpotProxy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def register(session: Session) -> None:
    register_method(
        CarrySpotProxy(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 3 — placeholder spot-proxy carry, awaiting futures (Stage 12)",
    )
