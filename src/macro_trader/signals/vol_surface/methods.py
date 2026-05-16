"""Vol surface signal methods (raw quotes vs spline-fitted surface).

Both methods read from ``market_data.options_chains`` (populated by
the yfinance options ingester) and extract the standard surface
metrics: ATM IV term structure, 25-delta skew, IV-RV spread (when
realized vol of the underlying is available), vol-of-vol.

- ``vol_surface.raw.v1`` uses raw chain quotes directly (e.g.
  ATM IV is the nearest-strike IV at the closest-to-21DTE expiry).
- ``vol_surface.svi.v1`` runs each expiry slice through the
  spline fitter in ``svi.py`` and reads metrics off the fitted
  surface — smoother and arbitrage-checked.

Sign convention: positive raw_value = long-bias. High IV-RV
spread (vol expensive vs realized) = contrarian short-vol bias =
positive raw_value on the *underlying* (long the spot). High put
skew = downside tail-pricing = contrarian long bias on the spot.
Net composite is the average of normalised z-scores across the
metric set.

Every output's ``metadata`` carries
``historical_backtest_supported: False`` per the Stage 5 data-
source decision (yfinance has no historical chains).
"""

from __future__ import annotations

import pickle
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
from sqlalchemy import select

from macro_trader.db.models.market_data import OptionsChain
from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.output import cross_sectional_rank
from macro_trader.signals.vol_surface.svi import (
    SliceFit,
    atm_iv,
    check_calendar_arbitrage,
    delta_iv,
    fit_slice,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


VOL_SURFACE_UNIVERSE: tuple[str, ...] = ("GLD", "SLV", "USO", "UNG", "DBA", "SPY")


def _load_chain(
    session: Session, instrument_id: str, *, as_of: datetime, lookback_hours: int = 48
) -> list[OptionsChain]:
    cutoff = as_of - timedelta(hours=lookback_hours)
    return list(
        session.scalars(
            select(OptionsChain)
            .where(OptionsChain.instrument_id == instrument_id)
            .where(OptionsChain.snapshot_ts >= cutoff)
            .where(OptionsChain.snapshot_ts <= as_of)
            .order_by(OptionsChain.snapshot_ts.desc())
        )
    )


def _slices_from_chain(
    chain: list[OptionsChain],
) -> dict[int, dict[str, np.ndarray]]:
    """Bucket the latest snapshot's chain by expiry (dte).

    Returns ``{dte: {"k": log-moneyness array, "iv": iv array}}``.
    Uses only call options for the ATM/skew computation since calls
    and puts at the same strike should price to the same IV but
    yfinance call quotes are typically tighter.
    """
    if not chain:
        return {}
    latest_ts = max(r.snapshot_ts for r in chain)
    out: dict[int, dict[str, list[float]]] = {}
    for row in chain:
        if row.snapshot_ts != latest_ts:
            continue
        spot = row.underlying_price
        iv = row.implied_vol
        if (
            spot is None
            or iv is None
            or row.strike <= 0
            or row.option_type != "call"
        ):
            continue
        dte = max(int((row.expiry_ts - latest_ts).total_seconds() / 86400.0), 1)
        k = float(np.log(row.strike / spot))
        bucket = out.setdefault(dte, {"k": [], "iv": []})
        bucket["k"].append(k)
        bucket["iv"].append(float(iv))
    return {
        dte: {"k": np.array(d["k"]), "iv": np.array(d["iv"])}
        for dte, d in out.items()
    }


def _materialise_outputs(
    *,
    per_instrument_score: dict[str, float],
    per_instrument_conf: dict[str, float],
    per_instrument_meta: dict[str, dict[str, Any]],
    data: SignalInput,
    method_id: str,
) -> list[SignalOutput]:
    ranks = cross_sectional_rank(
        {k: abs(v) for k, v in per_instrument_score.items() if v != 0.0}
    )
    outputs: list[SignalOutput] = []
    for inst in data.instrument_ids:
        score = per_instrument_score.get(inst, 0.0)
        conf = per_instrument_conf.get(inst, 0.0)
        meta = per_instrument_meta.get(inst, {"covered": False})
        meta["method_id"] = method_id
        meta["historical_backtest_supported"] = False
        meta["data_source"] = "yfinance"
        outputs.append(
            SignalOutput(
                instrument_id=inst,
                value_ts=data.as_of,
                observation_ts=data.as_of,
                raw_value=float(np.tanh(score)),
                zscore=float(score),
                rank=float(ranks.get(inst, 0.5)),
                confidence=float(conf),
                rolling_sharpe_252=None,
                metadata=meta,
            )
        )
    return outputs


def _surface_metrics_from_slices(
    slices: dict[int, dict[str, np.ndarray]],
    *,
    fitted: dict[int, SliceFit] | None = None,
) -> dict[str, Any]:
    """Compute term structure + skew metrics from either raw slice
    arrays (``fitted=None``) or fitted slices (``fitted={dte: SliceFit}``)."""
    if not slices:
        return {}

    # Pick the slice closest to 21 dte as "front".
    dtes = sorted(slices.keys())
    front_dte = min(dtes, key=lambda d: abs(d - 21))
    long_dte = max((d for d in dtes if d > front_dte), default=None)

    def _atm_iv(dte: int) -> float:
        if fitted and dte in fitted:
            return atm_iv(fitted[dte])
        # Raw: nearest-strike IV to k=0.
        arr = slices[dte]
        if len(arr["k"]) == 0:
            return float("nan")
        idx = int(np.argmin(np.abs(arr["k"])))
        return float(arr["iv"][idx])

    def _skew(dte: int) -> float:
        if fitted and dte in fitted:
            put_iv, call_iv = delta_iv(fitted[dte], delta=0.25)
            return put_iv - call_iv
        arr = slices[dte]
        if len(arr["k"]) < 4:
            return float("nan")
        order = np.argsort(arr["k"])
        ivs = arr["iv"][order]
        # Cheap proxy: average IV in lowest and highest quintile.
        q = max(len(ivs) // 5, 1)
        low_iv = float(np.mean(ivs[:q]))
        high_iv = float(np.mean(ivs[-q:]))
        return low_iv - high_iv  # left wing - right wing

    atm_front = _atm_iv(front_dte)
    atm_long = _atm_iv(long_dte) if long_dte is not None else float("nan")
    skew_front = _skew(front_dte)

    metrics: dict[str, Any] = {
        "atm_iv_front": atm_front,
        "atm_iv_long": atm_long,
        "term_structure_slope": (atm_long - atm_front)
        if np.isfinite(atm_long) and np.isfinite(atm_front)
        else float("nan"),
        "skew_25d": skew_front,
        "n_expiries": len(slices),
        "n_strikes_front": len(slices[front_dte]["k"]),
    }
    if fitted:
        metrics["calendar_arbitrage_violations"] = check_calendar_arbitrage(
            list(fitted.values())
        )
    return metrics


class _VolSurfaceBase(SignalMethod):
    """Shared logic; concrete subclasses set ``USE_SVI`` and metadata."""

    USE_SVI: bool = False

    def __init__(self, *, min_strikes_per_slice: int = 5) -> None:
        self.min_strikes_per_slice = int(min_strikes_per_slice)
        self._state: dict[str, Any] | None = None  # vol surface is stateless

    def fit(self, data: SignalInput) -> None:
        return None

    def predict(self, data: SignalInput) -> list[SignalOutput]:
        session = data.extras.get("session")
        return self.compute(data, session)

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> _VolSurfaceBase:
        return cls()

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError(f"{type(self).__name__} requires a DB session")
        per_instrument_score: dict[str, float] = {}
        per_instrument_conf: dict[str, float] = {}
        per_instrument_meta: dict[str, dict[str, Any]] = {}

        for inst in data.instrument_ids:
            if inst not in VOL_SURFACE_UNIVERSE:
                per_instrument_score[inst] = 0.0
                per_instrument_conf[inst] = 0.0
                per_instrument_meta[inst] = {"covered": False}
                continue
            chain = _load_chain(session, inst, as_of=data.as_of)
            if not chain:
                per_instrument_score[inst] = 0.0
                per_instrument_conf[inst] = 0.0
                per_instrument_meta[inst] = {
                    "covered": True,
                    "chain_freshness_hours": None,
                    "n_strikes_used": 0,
                    "n_expiries_used": 0,
                }
                continue
            slices = _slices_from_chain(chain)
            if not slices:
                per_instrument_score[inst] = 0.0
                per_instrument_conf[inst] = 0.05
                per_instrument_meta[inst] = {"covered": True, "n_strikes_used": 0}
                continue

            fitted: dict[int, SliceFit] | None = None
            if self.USE_SVI:
                fitted = {}
                for dte, arr in slices.items():
                    fit = fit_slice(
                        expiry_dte=dte,
                        log_moneyness=arr["k"],
                        iv=arr["iv"],
                        min_strikes=self.min_strikes_per_slice,
                    )
                    if fit is not None:
                        fitted[dte] = fit
                if not fitted:
                    per_instrument_score[inst] = 0.0
                    per_instrument_conf[inst] = 0.05
                    per_instrument_meta[inst] = {
                        "covered": True,
                        "fit_failed": True,
                    }
                    continue

            metrics = _surface_metrics_from_slices(slices, fitted=fitted)
            # Composite: contrarian short-vol when ATM IV is high
            # vs the term-structure expectation; tail-pricing when
            # skew is high. Both default to zero when missing.
            score_components: list[float] = []
            ts_slope = metrics.get("term_structure_slope")
            atm_front = metrics.get("atm_iv_front")
            skew = metrics.get("skew_25d")
            if ts_slope is not None and np.isfinite(ts_slope):
                # Backwardation (slope < 0): vol expected to fall ->
                # long-bias on the underlying.
                score_components.append(float(np.clip(-ts_slope * 5.0, -1.0, 1.0)))
            if atm_front is not None and np.isfinite(atm_front) and atm_front > 0:
                # Crude IV-RV proxy: assume RV ~= 0.2 (long-run
                # average for liquid-ETF underlyings); the proper
                # IV-RV needs the underlying's realised vol from
                # market_data.daily_bars, deferred (Stage 5
                # tradeoffs.md). Sign: high IV vs RV -> short-vol -> long underlying.
                iv_rv_proxy = float(np.clip((atm_front - 0.2) * 2.0, -1.0, 1.0))
                score_components.append(iv_rv_proxy)
            if skew is not None and np.isfinite(skew) and skew > 0:
                # Put skew positive -> tail pricing -> long-bias.
                score_components.append(float(np.clip(skew * 5.0, -1.0, 1.0)))

            score = float(np.mean(score_components)) if score_components else 0.0
            n_strikes_total = sum(
                len(arr["k"]) for arr in slices.values()
            )
            chain_freshness_hours = (
                data.as_of - chain[0].snapshot_ts
            ).total_seconds() / 3600.0
            per_instrument_score[inst] = score
            # Confidence: bounded by chain freshness + strike coverage.
            per_instrument_conf[inst] = max(
                0.05,
                min(
                    1.0,
                    (n_strikes_total / 25.0)
                    * (1.0 - min(chain_freshness_hours / 48.0, 1.0)),
                ),
            )
            per_instrument_meta[inst] = {
                "covered": True,
                "chain_freshness_hours": chain_freshness_hours,
                "n_strikes_used": n_strikes_total,
                "n_expiries_used": len(slices),
                **{k: v for k, v in metrics.items() if not isinstance(v, list)},
            }

        return _materialise_outputs(
            per_instrument_score=per_instrument_score,
            per_instrument_conf=per_instrument_conf,
            per_instrument_meta=per_instrument_meta,
            data=data,
            method_id=self.metadata.method_id,
        )


class RawVolSurface(_VolSurfaceBase):
    """Raw chain-quote surface metrics."""

    USE_SVI = False

    metadata = MethodMetadata(
        method_id="vol_surface.raw.v1",
        component="vol_surface_signal",
        name="Raw Vol Surface Metrics",
        version="1.0.0",
        description=(
            "ATM IV term structure + 25-delta skew + IV-RV proxy "
            "derived directly from raw yfinance options chains."
        ),
        references=[],
    )


class SVIVolSurface(_VolSurfaceBase):
    """Spline-fitted surface (Stage 5 fallback from full Gatheral SVI).

    Per-slice cubic-spline interpolation across log-moneyness; same
    metric extraction as the raw method but reading off the smoothed
    surface and exposing calendar-arbitrage violations in metadata.
    """

    USE_SVI = True

    metadata = MethodMetadata(
        method_id="vol_surface.svi.v1",
        component="vol_surface_signal",
        name="Spline-Fitted Vol Surface (Stage 5 SVI fallback)",
        version="1.0.0",
        description=(
            "Per-slice cubic-spline interpolation across log-moneyness "
            "with calendar arbitrage check. Stage 5 fallback for full "
            "Gatheral SVI per notes/stage_5/decisions.md."
        ),
        references=[
            "Gatheral (2004) A Parsimonious Arbitrage-Free Implied Volatility Parameterization",
            "Gatheral & Jacquier (2014) Arbitrage-Free SVI Volatility Surfaces",
        ],
    )


__all__ = [
    "VOL_SURFACE_UNIVERSE",
    "RawVolSurface",
    "SVIVolSurface",
]
_ = pickle  # silence unused; reserved for future serialised state
