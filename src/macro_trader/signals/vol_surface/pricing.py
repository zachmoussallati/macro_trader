"""Black-Scholes pricing + Greeks (pure functions).

yfinance returns option prices but not Greeks, and its implied-vol
values are often missing or stale. This module provides:

- :func:`bs_price` — Black-Scholes call / put price.
- :func:`implied_vol` — implied-vol solver via Brent's method.
- :func:`greeks` — delta, gamma, vega, theta (per-day theta).

All functions are pure / stateless and vectorise over numpy
inputs. They handle nonsense inputs (negative dte, zero spot)
by returning NaN rather than raising — the calling code (the
ingester) then drops those rows.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm


@dataclass(slots=True, frozen=True)
class OptionGreeks:
    delta: float
    gamma: float
    vega: float
    theta: float


def _d1_d2(
    spot: float, strike: float, t: float, r: float, q: float, sigma: float
) -> tuple[float, float]:
    sigma = max(sigma, 1e-9)
    t = max(t, 1e-9)
    d1 = (np.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / (
        sigma * np.sqrt(t)
    )
    d2 = d1 - sigma * np.sqrt(t)
    return d1, d2


def bs_price(
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    sigma: float,
    *,
    option_type: str,
) -> float:
    """Black-Scholes-Merton price for a European option.

    Args:
        spot: underlying price.
        strike: option strike.
        t: time to expiry in years.
        r: risk-free rate (continuous).
        q: continuous dividend / borrow yield.
        sigma: implied vol.
        option_type: ``"call"`` or ``"put"``.
    """
    if spot <= 0 or strike <= 0 or t <= 0 or sigma <= 0:
        return float("nan")
    d1, d2 = _d1_d2(spot, strike, t, r, q, sigma)
    df_q = np.exp(-q * t)
    df_r = np.exp(-r * t)
    if option_type == "call":
        return float(spot * df_q * norm.cdf(d1) - strike * df_r * norm.cdf(d2))
    if option_type == "put":
        return float(strike * df_r * norm.cdf(-d2) - spot * df_q * norm.cdf(-d1))
    raise ValueError(f"unknown option_type {option_type!r}")


def implied_vol(
    price: float,
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    *,
    option_type: str,
    lo: float = 1e-4,
    hi: float = 5.0,
) -> float:
    """Solve BS for sigma given a market price. Returns NaN if the
    price is outside the valid no-arbitrage interval."""
    if (
        price is None
        or not np.isfinite(price)
        or spot <= 0
        or strike <= 0
        or t <= 0
    ):
        return float("nan")
    f_lo = bs_price(spot, strike, t, r, q, lo, option_type=option_type) - price
    f_hi = bs_price(spot, strike, t, r, q, hi, option_type=option_type) - price
    if not np.isfinite(f_lo) or not np.isfinite(f_hi) or f_lo * f_hi > 0:
        return float("nan")
    try:
        return float(brentq(
            lambda s: bs_price(spot, strike, t, r, q, s, option_type=option_type) - price,
            lo, hi, xtol=1e-6, maxiter=64,
        ))
    except Exception:
        return float("nan")


def greeks(
    spot: float,
    strike: float,
    t: float,
    r: float,
    q: float,
    sigma: float,
    *,
    option_type: str,
) -> OptionGreeks:
    """Closed-form Black-Scholes Greeks. Theta is per-day (annualised
    theta divided by 365)."""
    if spot <= 0 or strike <= 0 or t <= 0 or sigma <= 0:
        return OptionGreeks(float("nan"), float("nan"), float("nan"), float("nan"))
    d1, d2 = _d1_d2(spot, strike, t, r, q, sigma)
    df_q = np.exp(-q * t)
    df_r = np.exp(-r * t)
    pdf_d1 = norm.pdf(d1)
    if option_type == "call":
        delta = df_q * norm.cdf(d1)
        theta_annual = (
            -spot * df_q * pdf_d1 * sigma / (2.0 * np.sqrt(t))
            - r * strike * df_r * norm.cdf(d2)
            + q * spot * df_q * norm.cdf(d1)
        )
    elif option_type == "put":
        delta = -df_q * norm.cdf(-d1)
        theta_annual = (
            -spot * df_q * pdf_d1 * sigma / (2.0 * np.sqrt(t))
            + r * strike * df_r * norm.cdf(-d2)
            - q * spot * df_q * norm.cdf(-d1)
        )
    else:
        raise ValueError(f"unknown option_type {option_type!r}")

    gamma = df_q * pdf_d1 / (spot * sigma * np.sqrt(t))
    vega = spot * df_q * pdf_d1 * np.sqrt(t)
    theta = theta_annual / 365.0
    return OptionGreeks(
        delta=float(delta),
        gamma=float(gamma),
        vega=float(vega),
        theta=float(theta),
    )


__all__ = ["OptionGreeks", "bs_price", "greeks", "implied_vol"]
