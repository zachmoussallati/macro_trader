"""Register data-quality methods through the framework.

Called by :func:`macro_trader.methods.setup.register_all_methods` on
Dagster code-location startup. Idempotent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.data.quality.methods import (
    IsolationForestOutlier,
    ZScoreOutlier,
)
from macro_trader.methods.registry import register_method
from macro_trader.methods.status import MethodStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def register(session: Session) -> None:
    """Register the z-score baseline and Isolation Forest shadow."""
    register_method(
        ZScoreOutlier(),
        MethodStatus.BASELINE,
        session=session,
        reason="Stage 2 — initial data quality baseline",
    )
    register_method(
        IsolationForestOutlier(),
        MethodStatus.SHADOW,
        session=session,
        reason="Stage 2 — Isolation Forest enhancement for data quality",
    )
