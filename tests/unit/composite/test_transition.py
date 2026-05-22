"""Unit tests for the transition-probability conviction dampener."""

from __future__ import annotations

import math

import pytest

from macro_trader.composite.transition import transition_multiplier_from_probability


def test_none_or_nan_fails_open() -> None:
    assert transition_multiplier_from_probability(None) == 1.0
    assert transition_multiplier_from_probability(float("nan")) == 1.0


def test_below_threshold_returns_one() -> None:
    assert transition_multiplier_from_probability(0.0) == 1.0
    assert transition_multiplier_from_probability(0.3) == 1.0
    assert transition_multiplier_from_probability(0.499, threshold=0.5) == 1.0


def test_at_threshold_returns_one() -> None:
    # By convention, prob == threshold returns 1.0 (not dampened yet).
    assert transition_multiplier_from_probability(0.5, threshold=0.5) == 1.0


def test_at_one_returns_floor() -> None:
    assert transition_multiplier_from_probability(1.0, floor=0.5) == 0.5
    assert transition_multiplier_from_probability(1.0, floor=0.2) == 0.2


def test_linear_interpolation_between_threshold_and_one() -> None:
    # Halfway: threshold=0.5, floor=0.5 -> at p=0.75 we're 50% of the
    # way down -> 1.0 - 0.5 * (1 - 0.5) = 0.75.
    m = transition_multiplier_from_probability(0.75, threshold=0.5, floor=0.5)
    assert math.isclose(m, 0.75, abs_tol=1e-9)
    # threshold=0.5, floor=0.0: at p=0.75 -> 1.0 - 0.5 * 1.0 = 0.5
    m = transition_multiplier_from_probability(0.75, threshold=0.5, floor=0.0)
    assert math.isclose(m, 0.5, abs_tol=1e-9)


@pytest.mark.parametrize("prob", [0.6, 0.7, 0.8, 0.9])
def test_monotone_decreasing_above_threshold(prob: float) -> None:
    earlier = transition_multiplier_from_probability(prob - 0.05)
    later = transition_multiplier_from_probability(prob)
    assert later <= earlier
