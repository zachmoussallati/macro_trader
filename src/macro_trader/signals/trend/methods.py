"""Trend signal methods.

- Three SMA crossover baselines (short/medium/long) on log-prices.
- HP-filter enhancement (shadow).
- Equal-weighted ensemble that combines the three SMAs into the
  designated production trend signal.

All four output the standardised :class:`SignalOutput` shape. ``raw_value``
is clipped to [-1, 1] via tanh so methods are comparable directly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from macro_trader.data.loaders import load_close_panel
from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.decay import rolling_sharpe
from macro_trader.signals.ensemble import equal_weighted
from macro_trader.signals.output import (
    cross_sectional_rank,
    rolling_zscore_of_self,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# ----------------------------------------------------------------------
# SMA crossover baseline
# ----------------------------------------------------------------------
class _SMACrossover(SignalMethod):
    """Generic SMA crossover. Concrete subclasses set fast / slow periods."""

    fast: int = 10
    slow: int = 30

    def __init__(self, *, fast: int | None = None, slow: int | None = None) -> None:
        if fast is not None:
            self.fast = fast
        if slow is not None:
            self.slow = slow
        if self.fast >= self.slow:
            raise ValueError(
                f"SMA fast ({self.fast}) must be strictly less than slow ({self.slow})"
            )

    # ------------------------------------------------------------------
    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("SMACrossover requires a DB session")
        # We need ``slow * 3`` history to compute the SMA + a decent z-score
        # / rolling-Sharpe lookback. ``slow * 6`` is generous.
        lookback_days = max(self.slow * 6, 365)
        load_start = data.start - timedelta(days=lookback_days)
        panel = load_close_panel(
            session,
            data.instrument_ids,
            start=load_start,
            end=data.end,
            as_of=data.as_of,
            calendar=data.calendar,
        )
        if panel.empty:
            return []
        log_prices = np.log(panel.replace(0, np.nan))
        fast_ma = log_prices.rolling(window=self.fast, min_periods=self.fast).mean()
        slow_ma = log_prices.rolling(window=self.slow, min_periods=self.slow).mean()
        signal_raw = np.tanh((fast_ma - slow_ma) * 10.0)  # squash to [-1, 1]

        return self._frame_to_outputs(
            signal_raw=signal_raw,
            log_prices=log_prices,
            data=data,
        )

    def _frame_to_outputs(
        self,
        *,
        signal_raw: pd.DataFrame,
        log_prices: pd.DataFrame,
        data: SignalInput,
    ) -> list[SignalOutput]:
        return _materialise_signal_outputs(
            signal_raw=signal_raw,
            log_prices=log_prices,
            data=data,
            confidence_factory=_confidence_from_completeness,
        )


class SMACrossoverShort(_SMACrossover):
    fast = 10
    slow = 30
    metadata = MethodMetadata(
        method_id="trend.sma_short.v1",
        component="trend_signal",
        name="SMA 10/30 crossover",
        version="1.0.0",
        description=(
            "Short-term momentum: log(close).rolling(10).mean() vs "
            "log(close).rolling(30).mean(), squashed via tanh."
        ),
        references=[],
    )


class SMACrossoverMedium(_SMACrossover):
    fast = 20
    slow = 60
    metadata = MethodMetadata(
        method_id="trend.sma_medium.v1",
        component="trend_signal",
        name="SMA 20/60 crossover",
        version="1.0.0",
        description="Medium-term SMA crossover (20/60), tanh-squashed.",
        references=[],
    )


class SMACrossoverLong(_SMACrossover):
    fast = 50
    slow = 200
    metadata = MethodMetadata(
        method_id="trend.sma_long.v1",
        component="trend_signal",
        name="SMA 50/200 (golden cross)",
        version="1.0.0",
        description="Long-term SMA crossover (50/200), tanh-squashed.",
        references=[],
    )


# ----------------------------------------------------------------------
# HP filter enhancement
# ----------------------------------------------------------------------
class HPFilterTrend(SignalMethod):
    """Hodrick-Prescott trend deviation.

    Signal = current log price minus the HP-filtered trend, normalised by
    rolling std and squashed via tanh. Positive = price above trend
    (potential mean-revert short OR continued trend — interpretation depends
    on regime; Stage 6 will refine).
    """

    metadata = MethodMetadata(
        method_id="trend.hp_filter.v1",
        component="trend_signal",
        name="Hodrick-Prescott trend deviation",
        version="1.0.0",
        description=(
            "Decompose log-price into trend + cycle with HP filter "
            "(lambda=1600). Signal = cycle / rolling_std(cycle), tanh-squashed."
        ),
        references=[
            "Hodrick, Prescott (1997): 'Postwar U.S. Business Cycles: An Empirical Investigation'",
        ],
    )

    def __init__(self, *, hp_lambda: float = 1600.0, normalisation_window: int = 60) -> None:
        self.hp_lambda = float(hp_lambda)
        self.normalisation_window = int(normalisation_window)

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("HPFilterTrend requires a DB session")
        load_start = data.start - timedelta(days=400)
        panel = load_close_panel(
            session,
            data.instrument_ids,
            start=load_start,
            end=data.end,
            as_of=data.as_of,
            calendar=data.calendar,
        )
        if panel.empty:
            return []
        log_prices = np.log(panel.replace(0, np.nan))
        cycle = pd.DataFrame(index=log_prices.index, columns=log_prices.columns, dtype=float)
        for col in log_prices.columns:
            series = log_prices[col].dropna()
            if len(series) < 60:
                continue
            cycle_col = _hp_filter_cycle(series.to_numpy(), self.hp_lambda)
            cycle[col] = pd.Series(cycle_col, index=series.index).reindex(log_prices.index)

        denom = cycle.rolling(window=self.normalisation_window, min_periods=20).std(ddof=1)
        normalised = (cycle / denom.replace(0, np.nan)).clip(lower=-5.0, upper=5.0)
        signal_raw = np.tanh(normalised)

        return _materialise_signal_outputs(
            signal_raw=signal_raw,
            log_prices=log_prices,
            data=data,
            confidence_factory=_confidence_from_completeness,
        )


# ----------------------------------------------------------------------
# Ensemble (designated production trend signal)
# ----------------------------------------------------------------------
class TrendEnsemble(SignalMethod):
    """Equal-weighted blend of the three SMA baselines.

    The ``regime_state`` parameter on :class:`SignalInput` is the interface
    Stage 6 will use to switch in regime-conditional weights. Stage 3 keeps
    weights fixed at 1/3 each and ignores the regime.
    """

    metadata = MethodMetadata(
        method_id="trend.ensemble.v1",
        component="trend_signal",
        name="Trend ensemble (3 SMAs, equal-weighted)",
        version="1.0.0",
        description=(
            "Equal-weighted blend of SMA 10/30, 20/60, 50/200. Designated as "
            "the production trend signal for downstream composite scoring "
            "(Stage 7)."
        ),
        references=[],
    )

    def __init__(self, *, components: Iterable[SignalMethod] | None = None) -> None:
        # By default this constructs its own SMA instances. Tests / runners
        # can inject pre-built instances to share data loading.
        self._components: list[SignalMethod] = (
            list(components)
            if components is not None
            else [SMACrossoverShort(), SMACrossoverMedium(), SMACrossoverLong()]
        )

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        per_component: dict[str, list[SignalOutput]] = {}
        for c in self._components:
            per_component[c.metadata.method_id] = c.compute(data, session)
        return equal_weighted(per_component, regime_state=data.regime_state)


# ----------------------------------------------------------------------
# Materialisation: turn a wide signal frame into SignalOutput rows.
# ----------------------------------------------------------------------
def _materialise_signal_outputs(
    *,
    signal_raw: pd.DataFrame,
    log_prices: pd.DataFrame,
    data: SignalInput,
    confidence_factory: Callable[[pd.Series, pd.Timestamp], float],
) -> list[SignalOutput]:
    """Common materialisation: clip to the requested [start, end] window,
    compute returns for the rolling Sharpe, then for each (instrument,
    value_ts) emit a SignalOutput row with cross-sectional rank applied at
    each timestamp."""

    # Restrict the output window now that we've consumed the prior history
    # we needed for SMAs / HP / rolling stats.
    start = pd.Timestamp(data.start).tz_convert("UTC")
    end = pd.Timestamp(data.end).tz_convert("UTC")
    window_mask = (signal_raw.index >= start) & (signal_raw.index <= end)
    if not window_mask.any():
        return []

    returns = log_prices.diff()

    rolling_per_inst: dict[str, pd.Series] = {}
    for col in signal_raw.columns:
        rolling_per_inst[col] = rolling_sharpe(
            signal_raw[col], returns[col], window=252, min_periods=60
        )

    # Per-instrument self-z over the same window (used as the SignalOutput.zscore).
    zscored = {
        col: rolling_zscore_of_self(signal_raw[col], lookback=252, min_periods=60)
        for col in signal_raw.columns
    }

    obs_ts = data.as_of
    outputs: list[SignalOutput] = []
    for value_ts in signal_raw.index[window_mask]:
        row_values: dict[str, float] = {}
        for col in signal_raw.columns:
            raw = signal_raw.at[value_ts, col]
            if raw is None or (isinstance(raw, float) and np.isnan(raw)):
                continue
            row_values[col] = float(raw)
        if not row_values:
            continue
        ranks = cross_sectional_rank(row_values)
        for col, raw in row_values.items():
            z = zscored[col].get(value_ts)
            z = float(z) if z is not None and not np.isnan(z) else 0.0
            rolling = rolling_per_inst[col].get(value_ts)
            rolling_v = float(rolling) if rolling is not None and not np.isnan(rolling) else None
            outputs.append(
                SignalOutput(
                    instrument_id=col,
                    value_ts=value_ts.to_pydatetime(),
                    observation_ts=obs_ts,
                    raw_value=float(raw),
                    zscore=z,
                    rank=float(ranks.get(col, 0.5)),
                    confidence=float(confidence_factory(log_prices[col], value_ts)),
                    rolling_sharpe_252=rolling_v,
                    metadata={},
                )
            )
    return outputs


def _confidence_from_completeness(
    series: pd.Series, value_ts: pd.Timestamp, *, lookback: int = 252
) -> float:
    """Confidence = fraction of non-NaN observations in the trailing window."""
    if series.empty:
        return 0.0
    upto = series.loc[:value_ts]
    if len(upto) == 0:
        return 0.0
    window = upto.iloc[-lookback:]
    if window.empty:
        return 0.0
    return float(window.notna().mean())


# ----------------------------------------------------------------------
# HP filter implementation (small, dependency-free)
# ----------------------------------------------------------------------
def _hp_filter_cycle(y: np.ndarray, hp_lambda: float) -> np.ndarray:
    """Return the cyclical component (y - trend) of the HP filter.

    Solves ``(I + lambda * K' K) trend = y`` directly with numpy's
    banded solver via ``np.linalg.solve``. Works fine for the 400-day
    windows we feed it; for very long series, prefer the sparse version
    in :mod:`statsmodels.tsa.filters.hp_filter` but it's not needed at
    Stage 3 scale.
    """
    n = len(y)
    if n < 5:
        return np.zeros_like(y)
    # k is the (n-2, n) second-difference matrix.
    k = np.zeros((n - 2, n))
    for i in range(n - 2):
        k[i, i] = 1.0
        k[i, i + 1] = -2.0
        k[i, i + 2] = 1.0
    lhs = np.eye(n) + hp_lambda * (k.T @ k)
    trend = np.linalg.solve(lhs, y)
    return y - trend


__all__ = [
    "HPFilterTrend",
    "SMACrossoverLong",
    "SMACrossoverMedium",
    "SMACrossoverShort",
    "TrendEnsemble",
]
