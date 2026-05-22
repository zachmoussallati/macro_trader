"""Drawdown-gate state machine.

Three staged thresholds:

| Level | Trigger                          | Effect                | Auto-release      |
| ----- | -------------------------------- | --------------------- | ----------------- |
| 1     | daily return <= -5%              | scaling_factor = 0.5  | after 3 days      |
| 2     | trailing 5-day return <= -8%     | scaling_factor = 0.3  | after 10 days     |
| 3     | drawdown from peak <= -10%       | scaling_factor = 0.0  | **manual only**   |

When multiple levels are active they compound (level_1 + level_2 ->
factor 0.15). Level 3 zeroes out everything regardless of other
levels.

The state is queried daily by the position runner before persisting
to ``portfolio.positions``. Operators interact with it via the
POST /api/v1/portfolio/drawdown/release endpoint (level 3 only;
levels 1 and 2 auto-release).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

GateLevel = Literal["none", "level_1", "level_2", "level_3"]


@dataclass(slots=True)
class GateLevelState:
    """Single-level gate state — three of these compose into a full
    :class:`DrawdownState`."""

    level: GateLevel
    triggered_at: datetime | None
    release_at: datetime | None  # None for level_3 (manual)
    scaling_factor: float

    @property
    def is_active(self) -> bool:
        return self.level != "none"


@dataclass(slots=True)
class DrawdownState:
    """Composite state across all three levels.

    ``effective_scaling_factor`` is the product of the active levels'
    factors. ``current_level`` is the most-severe currently-active
    level (level_3 > level_2 > level_1 > none).
    """

    level_1: GateLevelState
    level_2: GateLevelState
    level_3: GateLevelState
    as_of: datetime
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def effective_scaling_factor(self) -> float:
        factor = 1.0
        for st in (self.level_1, self.level_2, self.level_3):
            if st.is_active:
                factor *= st.scaling_factor
        return float(factor)

    @property
    def current_level(self) -> GateLevel:
        if self.level_3.is_active:
            return "level_3"
        if self.level_2.is_active:
            return "level_2"
        if self.level_1.is_active:
            return "level_1"
        return "none"


@dataclass(slots=True, frozen=True)
class GateConfig:
    """Parameter bundle (mirrors ``portfolio.drawdown.*`` config)."""

    level_1_daily_threshold: float = -0.05
    level_1_scaling_factor: float = 0.5
    level_1_release_after_days: int = 3
    level_2_cumulative_window: int = 5
    level_2_cumulative_threshold: float = -0.08
    level_2_scaling_factor: float = 0.3
    level_2_release_after_days: int = 10
    level_3_peak_drawdown_threshold: float = -0.10
    level_3_scaling_factor: float = 0.0


def _empty_level(level: GateLevel) -> GateLevelState:
    return GateLevelState(
        level="none",
        triggered_at=None,
        release_at=None,
        scaling_factor=1.0,
    )


def empty_state(as_of: datetime) -> DrawdownState:
    """Fresh state — no levels active."""
    return DrawdownState(
        level_1=_empty_level("level_1"),
        level_2=_empty_level("level_2"),
        level_3=_empty_level("level_3"),
        as_of=as_of,
    )


def evaluate_gate(
    prior_state: DrawdownState,
    *,
    as_of: datetime,
    daily_return: float | None,
    rolling_5d_return: float | None,
    drawdown_from_peak: float | None,
    config: GateConfig | None = None,
    level_3_manual_release_requested: bool = False,
) -> DrawdownState:
    """Apply the staged trigger + auto-release logic.

    Pure function: returns the new state without mutating
    ``prior_state``. The caller persists.

    ``level_3_manual_release_requested`` is the toggle the API hook
    flips when an operator confirms a level-3 restart; the function
    only releases level 3 when it is set.
    """
    cfg = config or GateConfig()

    new_state = DrawdownState(
        level_1=GateLevelState(
            level=prior_state.level_1.level,
            triggered_at=prior_state.level_1.triggered_at,
            release_at=prior_state.level_1.release_at,
            scaling_factor=prior_state.level_1.scaling_factor,
        ),
        level_2=GateLevelState(
            level=prior_state.level_2.level,
            triggered_at=prior_state.level_2.triggered_at,
            release_at=prior_state.level_2.release_at,
            scaling_factor=prior_state.level_2.scaling_factor,
        ),
        level_3=GateLevelState(
            level=prior_state.level_3.level,
            triggered_at=prior_state.level_3.triggered_at,
            release_at=prior_state.level_3.release_at,
            scaling_factor=prior_state.level_3.scaling_factor,
        ),
        as_of=as_of,
        metadata=dict(prior_state.metadata),
    )

    # --- Auto-release first (avoid re-triggering the same day) -----
    if (
        new_state.level_1.is_active
        and new_state.level_1.release_at is not None
        and as_of >= new_state.level_1.release_at
    ):
        new_state.level_1 = _empty_level("level_1")
    if (
        new_state.level_2.is_active
        and new_state.level_2.release_at is not None
        and as_of >= new_state.level_2.release_at
    ):
        new_state.level_2 = _empty_level("level_2")
    if new_state.level_3.is_active and level_3_manual_release_requested:
        new_state.level_3 = _empty_level("level_3")

    # --- Trigger logic --------------------------------------------
    if (
        daily_return is not None
        and daily_return <= cfg.level_1_daily_threshold
        and not new_state.level_1.is_active
    ):
        new_state.level_1 = GateLevelState(
            level="level_1",
            triggered_at=as_of,
            release_at=as_of + timedelta(days=cfg.level_1_release_after_days),
            scaling_factor=cfg.level_1_scaling_factor,
        )

    if (
        rolling_5d_return is not None
        and rolling_5d_return <= cfg.level_2_cumulative_threshold
        and not new_state.level_2.is_active
    ):
        new_state.level_2 = GateLevelState(
            level="level_2",
            triggered_at=as_of,
            release_at=as_of + timedelta(days=cfg.level_2_release_after_days),
            scaling_factor=cfg.level_2_scaling_factor,
        )

    if (
        drawdown_from_peak is not None
        and drawdown_from_peak <= cfg.level_3_peak_drawdown_threshold
        and not new_state.level_3.is_active
    ):
        new_state.level_3 = GateLevelState(
            level="level_3",
            triggered_at=as_of,
            release_at=None,  # manual only
            scaling_factor=cfg.level_3_scaling_factor,
        )

    return new_state


__all__ = [
    "DrawdownState",
    "GateConfig",
    "GateLevel",
    "GateLevelState",
    "empty_state",
    "evaluate_gate",
]
