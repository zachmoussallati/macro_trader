"""Black-Scholes pricing + Greeks (pure-numpy tests, no DB)."""

from __future__ import annotations

import numpy as np
import pytest

from macro_trader.signals.vol_surface.pricing import (
    bs_price,
    greeks,
    implied_vol,
)


@pytest.mark.unit
def test_bs_price_call_atm_one_year_volatile() -> None:
    """ATM 1y call at 20% vol with r=0, q=0 prices to ~spot * 0.0796
    (the standard BS approximation)."""
    spot = 100.0
    call = bs_price(spot, 100.0, 1.0, 0.0, 0.0, 0.2, option_type="call")
    assert 7.0 < call < 9.0


@pytest.mark.unit
def test_bs_price_put_call_parity_at_zero_rates() -> None:
    """At r=q=0, call(K) - put(K) = spot - K."""
    spot = 100.0
    for strike in (80.0, 100.0, 120.0):
        c = bs_price(spot, strike, 0.5, 0.0, 0.0, 0.25, option_type="call")
        p = bs_price(spot, strike, 0.5, 0.0, 0.0, 0.25, option_type="put")
        assert abs((c - p) - (spot - strike)) < 1e-6


@pytest.mark.unit
def test_bs_price_returns_nan_on_invalid_inputs() -> None:
    assert np.isnan(bs_price(-1, 100, 1, 0, 0, 0.2, option_type="call"))
    assert np.isnan(bs_price(100, 0, 1, 0, 0, 0.2, option_type="call"))
    assert np.isnan(bs_price(100, 100, 0, 0, 0, 0.2, option_type="call"))
    assert np.isnan(bs_price(100, 100, 1, 0, 0, 0, option_type="call"))


@pytest.mark.unit
def test_implied_vol_recovers_input_sigma() -> None:
    """Price an option at known sigma; solver recovers it within tol."""
    spot, strike, t, r, q = 100.0, 105.0, 0.5, 0.04, 0.0
    for sigma in (0.10, 0.20, 0.35, 0.50):
        for option_type in ("call", "put"):
            price = bs_price(spot, strike, t, r, q, sigma, option_type=option_type)
            iv = implied_vol(
                price, spot, strike, t, r, q, option_type=option_type
            )
            assert abs(iv - sigma) < 1e-3, (
                f"recovered iv={iv:.5f} expected {sigma:.5f} type={option_type}"
            )


@pytest.mark.unit
def test_implied_vol_returns_nan_when_price_outside_no_arb() -> None:
    # Call price below intrinsic: nonsense, solver should give up.
    iv = implied_vol(
        price=0.01, spot=100, strike=80, t=1.0, r=0.0, q=0.0, option_type="call"
    )
    assert np.isnan(iv)


@pytest.mark.unit
def test_greeks_signs_match_textbook() -> None:
    spot, strike, t, r, q, sigma = 100.0, 100.0, 0.5, 0.04, 0.0, 0.25
    g_call = greeks(spot, strike, t, r, q, sigma, option_type="call")
    g_put = greeks(spot, strike, t, r, q, sigma, option_type="put")
    assert 0 < g_call.delta < 1
    assert -1 < g_put.delta < 0
    assert g_call.gamma > 0 and g_put.gamma > 0
    assert g_call.vega > 0 and g_put.vega > 0
    # Theta is per-day; both are negative for long options (time decay).
    assert g_call.theta < 0 and g_put.theta < 0


@pytest.mark.unit
def test_greeks_return_nan_on_invalid_inputs() -> None:
    g = greeks(0, 100, 1, 0, 0, 0.2, option_type="call")
    assert np.isnan(g.delta) and np.isnan(g.gamma)
