"""Alt-data family registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus
from macro_trader.signals.alt_data.methods import (
    EIAStorageSurprise,
    GoogleTrendsSentiment,
    USDAWASDESurprise,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def register(session: Session) -> None:
    """Register the three alt-data signals.

    All three are BASELINE — there's no "shadow" structure within the
    family because each signal targets a different commodity subset.
    Composite scoring in Stage 7 combines them via standard z * conf.
    """
    register_method(
        EIAStorageSurprise(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 5 - EIA weekly storage surprise",
    )
    register_method(
        USDAWASDESurprise(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 5 - USDA WASDE production surprise",
    )
    register_method(
        GoogleTrendsSentiment(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 5 - Google Trends contrarian sentiment",
    )
