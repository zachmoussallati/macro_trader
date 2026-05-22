"""Unit tests for the drawdown-gate state machine + detector.

Pure-Python, no DB — these cover the trigger / auto-release logic
and the equity-curve math. The DB-bound runner is exercised by
``tests/integration/portfolio/test_portfolio_pipeline.py`` (Stage 8
Phase 6 integration test, follow-on).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from macro_trader.portfolio.drawdown.detector import (
    build_equity_curve,
    peak_drawdown,
    rolling_window_return,
)
from macro_trader.portfolio.drawdown.gates import (
    DrawdownState,
    GateConfig,
    empty_state,
    evaluate_gate,
)


def _day(i: int) -> datetime:
    return datetime(2026, 1, 1) + timedelta(days=i)


# ----------------------------------------------------------------------
# Detector / equity curve math
# ----------------------------------------------------------------------
class TestDetector:
    def test_empty_curve(self) -> None:
        assert build_equity_curve([]) == []
        assert peak_drawdown([]) == 0.0
        assert rolling_window_return([], window=5) is None

    def test_single_positive_day(self) -> None:
        curve = build_equity_curve([(_day(0), 0.01)])
        assert len(curve) == 1
        assert math.isclose(curve[0].nav, 1.01, abs_tol=1e-9)
        assert math.isclose(curve[0].peak_nav, 1.01, abs_tol=1e-9)
        # NAV is at the peak, so drawdown is 0.
        assert math.isclose(curve[0].drawdown_from_peak, 0.0, abs_tol=1e-9)
        assert math.isclose(curve[0].cumulative_return, 0.01, abs_tol=1e-9)

    def test_single_negative_day_creates_drawdown(self) -> None:
        # Without a prior peak, the first day's NAV is the peak —
        # even a negative return doesn't show as drawdown until
        # subsequent rallies fail to recover.
        curve = build_equity_curve([(_day(0), -0.05)])
        assert math.isclose(curve[0].nav, 0.95, abs_tol=1e-9)
        assert math.isclose(curve[0].peak_nav, 1.0, abs_tol=1e-9)
        assert math.isclose(curve[0].drawdown_from_peak, -0.05, abs_tol=1e-9)

    def test_peak_tracks_running_max(self) -> None:
        # Up 1%, up 1%, down 2% — peak is the post-second-day NAV;
        # drawdown is from there.
        rets = [(_day(0), 0.01), (_day(1), 0.01), (_day(2), -0.02)]
        curve = build_equity_curve(rets)
        nav_after_up = 1.01 * 1.01
        nav_after_down = nav_after_up * 0.98
        assert math.isclose(curve[2].peak_nav, nav_after_up, abs_tol=1e-9)
        assert math.isclose(curve[2].nav, nav_after_down, abs_tol=1e-9)
        expected_dd = (nav_after_down - nav_after_up) / nav_after_up
        assert math.isclose(
            curve[2].drawdown_from_peak, expected_dd, abs_tol=1e-9
        )

    def test_peak_drawdown_returns_min(self) -> None:
        rets = [
            (_day(0), 0.01),
            (_day(1), -0.03),
            (_day(2), -0.02),
            (_day(3), 0.01),
        ]
        curve = build_equity_curve(rets)
        # Curve troughs after day 2: drawdown should be most negative
        # at that point.
        worst = peak_drawdown(curve)
        assert worst < 0
        assert worst <= curve[2].drawdown_from_peak

    def test_rolling_window_return(self) -> None:
        rets = [0.01, 0.01, -0.02, -0.01, -0.05]
        # Trailing 5-day compound: (1.01)(1.01)(0.98)(0.99)(0.95) - 1.
        expected = 1.01 * 1.01 * 0.98 * 0.99 * 0.95 - 1.0
        actual = rolling_window_return(rets, window=5)
        assert actual is not None
        assert math.isclose(actual, expected, abs_tol=1e-9)

    def test_rolling_window_too_short(self) -> None:
        rets = [0.01, 0.01]
        assert rolling_window_return(rets, window=5) is None


# ----------------------------------------------------------------------
# Gate state machine
# ----------------------------------------------------------------------
class TestGateTriggers:
    def test_empty_state(self) -> None:
        s = empty_state(_day(0))
        assert s.current_level == "none"
        assert s.effective_scaling_factor == 1.0

    def test_level_1_triggers_on_5pct_daily_loss(self) -> None:
        prior = empty_state(_day(0))
        s = evaluate_gate(
            prior,
            as_of=_day(1),
            daily_return=-0.06,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        assert s.current_level == "level_1"
        assert s.level_1.is_active
        assert s.level_1.scaling_factor == 0.5
        assert s.effective_scaling_factor == 0.5
        assert s.level_1.release_at == _day(1) + timedelta(days=3)

    def test_level_1_does_not_trigger_above_threshold(self) -> None:
        prior = empty_state(_day(0))
        s = evaluate_gate(
            prior,
            as_of=_day(1),
            daily_return=-0.04,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        assert s.current_level == "none"

    def test_level_2_triggers_on_5day_8pct(self) -> None:
        prior = empty_state(_day(0))
        s = evaluate_gate(
            prior,
            as_of=_day(1),
            daily_return=None,
            rolling_5d_return=-0.085,
            drawdown_from_peak=None,
        )
        assert s.current_level == "level_2"
        assert s.level_2.scaling_factor == 0.3
        assert s.effective_scaling_factor == 0.3

    def test_level_3_triggers_on_10pct_peak_drawdown(self) -> None:
        prior = empty_state(_day(0))
        s = evaluate_gate(
            prior,
            as_of=_day(1),
            daily_return=None,
            rolling_5d_return=None,
            drawdown_from_peak=-0.11,
        )
        assert s.current_level == "level_3"
        assert s.level_3.scaling_factor == 0.0
        assert s.level_3.release_at is None
        assert s.effective_scaling_factor == 0.0

    def test_gates_compound(self) -> None:
        """Level 1 + level 2 active at the same time -> factor 0.5 * 0.3."""
        prior = empty_state(_day(0))
        s = evaluate_gate(
            prior,
            as_of=_day(1),
            daily_return=-0.06,
            rolling_5d_return=-0.085,
            drawdown_from_peak=None,
        )
        assert s.level_1.is_active
        assert s.level_2.is_active
        assert math.isclose(s.effective_scaling_factor, 0.15, abs_tol=1e-9)

    def test_level_3_zeroes_out_regardless_of_other_levels(self) -> None:
        prior = empty_state(_day(0))
        s = evaluate_gate(
            prior,
            as_of=_day(1),
            daily_return=-0.06,
            rolling_5d_return=-0.085,
            drawdown_from_peak=-0.12,
        )
        assert s.level_1.is_active
        assert s.level_2.is_active
        assert s.level_3.is_active
        assert s.effective_scaling_factor == 0.0


class TestGateReleases:
    def test_level_1_auto_releases_after_3_days(self) -> None:
        # Trigger on day 1; release window ends at day 4.
        prior = evaluate_gate(
            empty_state(_day(0)),
            as_of=_day(1),
            daily_return=-0.06,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        # Day 3: still active.
        s = evaluate_gate(
            prior,
            as_of=_day(3),
            daily_return=0.0,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        assert s.level_1.is_active
        # Day 4: auto-release fires.
        s = evaluate_gate(
            prior,
            as_of=_day(4),
            daily_return=0.0,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        assert not s.level_1.is_active
        assert s.effective_scaling_factor == 1.0

    def test_level_2_auto_releases_after_10_days(self) -> None:
        prior = evaluate_gate(
            empty_state(_day(0)),
            as_of=_day(1),
            daily_return=None,
            rolling_5d_return=-0.085,
            drawdown_from_peak=None,
        )
        # Day 10: still active.
        s = evaluate_gate(
            prior,
            as_of=_day(10),
            daily_return=None,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        assert s.level_2.is_active
        # Day 11: auto-release fires.
        s = evaluate_gate(
            prior,
            as_of=_day(11),
            daily_return=None,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        assert not s.level_2.is_active

    def test_level_3_requires_manual_release(self) -> None:
        prior = evaluate_gate(
            empty_state(_day(0)),
            as_of=_day(1),
            daily_return=None,
            rolling_5d_return=None,
            drawdown_from_peak=-0.12,
        )
        # 30 days later, still active without manual release.
        s = evaluate_gate(
            prior,
            as_of=_day(31),
            daily_return=0.0,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        assert s.level_3.is_active
        # With manual release flag: cleared.
        s = evaluate_gate(
            prior,
            as_of=_day(31),
            daily_return=0.0,
            rolling_5d_return=None,
            drawdown_from_peak=None,
            level_3_manual_release_requested=True,
        )
        assert not s.level_3.is_active
        assert s.effective_scaling_factor == 1.0


class TestGateIdempotence:
    def test_re_trigger_does_not_extend_active_gate(self) -> None:
        """If level 1 is already active and we evaluate again with another
        -5% day, the original triggered_at / release_at should not change."""
        s1 = evaluate_gate(
            empty_state(_day(0)),
            as_of=_day(1),
            daily_return=-0.06,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        triggered = s1.level_1.triggered_at
        release = s1.level_1.release_at

        s2 = evaluate_gate(
            s1,
            as_of=_day(2),
            daily_return=-0.06,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        assert s2.level_1.is_active
        assert s2.level_1.triggered_at == triggered
        assert s2.level_1.release_at == release

    def test_pure_function_does_not_mutate_prior(self) -> None:
        prior = empty_state(_day(0))
        assert prior.current_level == "none"
        _ = evaluate_gate(
            prior,
            as_of=_day(1),
            daily_return=-0.06,
            rolling_5d_return=None,
            drawdown_from_peak=None,
        )
        # Prior must still be empty.
        assert prior.current_level == "none"


@pytest.mark.parametrize(
    "daily,rolling,peak,expected_level,expected_factor",
    [
        (None, None, None, "none", 1.0),
        (-0.04, None, None, "none", 1.0),
        (-0.05, None, None, "level_1", 0.5),
        (None, -0.08, None, "level_2", 0.3),
        (None, None, -0.10, "level_3", 0.0),
        (-0.05, -0.08, None, "level_2", 0.15),
        (-0.05, -0.08, -0.10, "level_3", 0.0),
    ],
)
def test_gate_truth_table(
    daily: float | None,
    rolling: float | None,
    peak: float | None,
    expected_level: str,
    expected_factor: float,
) -> None:
    cfg = GateConfig()
    s = evaluate_gate(
        empty_state(_day(0)),
        as_of=_day(1),
        daily_return=daily,
        rolling_5d_return=rolling,
        drawdown_from_peak=peak,
        config=cfg,
    )
    assert s.current_level == expected_level
    assert math.isclose(s.effective_scaling_factor, expected_factor, abs_tol=1e-9)


def test_dataclass_field_default() -> None:
    """Sanity: DrawdownState exposes a metadata dict default."""
    s = empty_state(_day(0))
    assert isinstance(s, DrawdownState)
    assert s.metadata == {}
