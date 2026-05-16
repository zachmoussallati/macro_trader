"""Per-slice spline fitter tests."""

from __future__ import annotations

import numpy as np
import pytest

from macro_trader.signals.vol_surface.svi import (
    SliceFit,
    atm_iv,
    check_calendar_arbitrage,
    delta_iv,
    fit_slice,
    predict_slice,
)


@pytest.mark.unit
def test_fit_slice_returns_none_when_too_few_strikes() -> None:
    fit = fit_slice(
        expiry_dte=30,
        log_moneyness=np.array([-0.1, 0.0, 0.1]),
        iv=np.array([0.25, 0.20, 0.22]),
        min_strikes=5,
    )
    assert fit is None


@pytest.mark.unit
def test_fit_slice_returns_none_for_nonpositive_dte() -> None:
    fit = fit_slice(
        expiry_dte=0,
        log_moneyness=np.linspace(-0.2, 0.2, 7),
        iv=np.linspace(0.25, 0.20, 7),
        min_strikes=5,
    )
    assert fit is None


@pytest.mark.unit
def test_fit_slice_round_trip_on_quadratic_smile() -> None:
    """Fit a quadratic smile and verify predict_slice at the knot
    points recovers (approximately) the input total variance."""
    k = np.linspace(-0.4, 0.4, 9)
    iv = 0.20 + 0.5 * k * k   # quadratic smile
    dte = 30
    fit = fit_slice(expiry_dte=dte, log_moneyness=k, iv=iv, min_strikes=5)
    assert fit is not None
    assert fit.n_strikes == 9
    # At the knots the spline returns the stored total variance
    # exactly.
    t = dte / 365.0
    pred = predict_slice(fit, k)
    expected = (iv * iv) * t
    np.testing.assert_allclose(pred, expected, atol=1e-9)


@pytest.mark.unit
def test_atm_iv_recovers_input_atm() -> None:
    """ATM IV from a fitted slice at k=0 matches the input ATM IV."""
    k = np.linspace(-0.4, 0.4, 9)
    iv = 0.20 + 0.5 * k * k
    fit = fit_slice(expiry_dte=30, log_moneyness=k, iv=iv, min_strikes=5)
    assert fit is not None
    assert abs(atm_iv(fit) - 0.20) < 1e-6


@pytest.mark.unit
def test_delta_iv_returns_finite_for_reasonable_smile() -> None:
    k = np.linspace(-0.4, 0.4, 9)
    iv = 0.20 + 0.5 * k * k
    fit = fit_slice(expiry_dte=30, log_moneyness=k, iv=iv, min_strikes=5)
    assert fit is not None
    put_iv, call_iv = delta_iv(fit, delta=0.25)
    assert np.isfinite(put_iv) and np.isfinite(call_iv)


@pytest.mark.unit
def test_check_calendar_arbitrage_flags_violation() -> None:
    """Build an early-expiry slice with higher total variance than a
    later slice and confirm the arb check flags it."""
    k = np.linspace(-0.2, 0.2, 7)
    # Early slice: 30 dte at 50% IV -> total var huge
    early = fit_slice(
        expiry_dte=30,
        log_moneyness=k,
        iv=np.full_like(k, 0.50),
        min_strikes=5,
    )
    # Late slice: 60 dte at 20% IV -> much smaller total var (arb!)
    late = fit_slice(
        expiry_dte=60,
        log_moneyness=k,
        iv=np.full_like(k, 0.20),
        min_strikes=5,
    )
    assert early is not None and late is not None
    violations = check_calendar_arbitrage([early, late])
    assert violations
    assert violations[0]["expiry_dte"] == 60
    assert violations[0]["n_violations"] > 0


@pytest.mark.unit
def test_check_calendar_arbitrage_clean_when_monotonic() -> None:
    """Total variance increasing with expiry => no violations."""
    k = np.linspace(-0.2, 0.2, 7)
    early = fit_slice(expiry_dte=30, log_moneyness=k, iv=np.full_like(k, 0.20), min_strikes=5)
    late = fit_slice(expiry_dte=60, log_moneyness=k, iv=np.full_like(k, 0.25), min_strikes=5)
    assert early is not None and late is not None
    assert check_calendar_arbitrage([early, late]) == []


@pytest.mark.unit
def test_slice_fit_dataclass_round_trip_via_dict() -> None:
    fit = SliceFit(
        expiry_dte=30,
        log_moneyness=[-0.1, 0.0, 0.1],
        implied_var=[0.005, 0.003, 0.005],
        n_strikes=3,
        fit_rmse=0.0,
    )
    # Used for round-trip via the JSONB parameters column.
    d = {
        "expiry_dte": fit.expiry_dte,
        "log_moneyness": fit.log_moneyness,
        "implied_var": fit.implied_var,
        "n_strikes": fit.n_strikes,
        "fit_rmse": fit.fit_rmse,
    }
    reconstructed = SliceFit(**d)
    assert reconstructed == fit
