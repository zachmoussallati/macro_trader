"""Value-family method registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.signals.value.methods import (
    CrossSectionalValue,
    ZScoreValue,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def register(session: Session) -> None:
    register_method(
        ZScoreValue(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 3 — rolling z-score value baseline",
    )
    register_method(
        CrossSectionalValue(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 3 — cross-sectional value enhancement (sub-class ranked)",
    )
