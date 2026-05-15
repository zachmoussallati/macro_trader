"""Value signal methods.

- ``value.zscore.v1`` — BASELINE: rolling z-score of log-price vs N-day mean,
  inverted so positive = cheap (mean-revert long).
- ``value.cross_sectional.v1`` — SHADOW: cross-sectional rank of the
  underlying z-score within each asset sub-class, removing sector-wide
  drift before ranking.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from macro_trader.data.loaders import load_close_panel
from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.decay import rolling_sharpe
from macro_trader.signals.output import (
    cross_sectional_rank,
    rolling_zscore_of_self,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# Stage 4A: cross-sectional groupings now come from the
# ``market_data.instruments`` table at compute time via
# ``get_class_groups``. The previous hardcoded dict has been removed; the
# config block ``signals.value.cross_sectional.sub_class_groups`` is no
# longer read.


# ----------------------------------------------------------------------
# Helpers shared by both methods
# ----------------------------------------------------------------------
def _rolling_zscore_panel(panel: pd.DataFrame, *, window: int) -> pd.DataFrame:
    """Per-column rolling z-score, with NaNs preserved in cold-start window."""
    if panel.empty:
        return panel
    mu = panel.rolling(window=window, min_periods=max(20, window // 4)).mean()
    sigma = (
        panel.rolling(window=window, min_periods=max(20, window // 4))
        .std(ddof=1)
        .replace(0, np.nan)
    )
    return (panel - mu) / sigma


def _confidence_from_completeness(
    series: pd.Series, value_ts: pd.Timestamp, *, lookback: int
) -> float:
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
# Baseline: rolling z-score (inverted)
# ----------------------------------------------------------------------
class ZScoreValue(SignalMethod):
    metadata = MethodMetadata(
        method_id="value.zscore.v1",
        component="value_signal",
        name="Rolling z-score value",
        version="1.0.0",
        description=(
            "Rolling z-score of log-price vs N-day mean (default 252). "
            "Inverted: positive value = cheap (mean-revert long), "
            "negative = expensive."
        ),
        references=[],
    )

    def __init__(self, *, lookback_window: int = 252) -> None:
        self.lookback_window = int(lookback_window)

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("ZScoreValue requires a DB session")
        load_start = data.start - timedelta(days=self.lookback_window + 60)
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
        raw_z = _rolling_zscore_panel(log_prices, window=self.lookback_window)
        # Inverted so cheap = positive (mean-revert long).
        signal_raw = np.tanh(-raw_z.clip(lower=-3.0, upper=3.0))

        return _materialise_value_outputs(
            signal_raw=signal_raw,
            log_prices=log_prices,
            data=data,
            sub_class_groups=None,  # baseline is universe-ranked
        )


# ----------------------------------------------------------------------
# Shadow: cross-sectional rank within sub-class
# ----------------------------------------------------------------------
class CrossSectionalValue(SignalMethod):
    metadata = MethodMetadata(
        method_id="value.cross_sectional.v1",
        component="value_signal",
        name="Cross-sectional value rank within sub-class",
        version="1.0.0",
        description=(
            "Same underlying z-score as ZScoreValue but ranked within asset "
            "sub-class (energy / base_metals / precious_metals / agriculture). "
            "Removes sector-wide drift before ranking."
        ),
        references=[],
    )

    def __init__(
        self,
        *,
        lookback_window: int = 252,
        class_column: str = "asset_class",
    ) -> None:
        self.lookback_window = int(lookback_window)
        self.class_column = class_column

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("CrossSectionalValue requires a DB session")
        from macro_trader.data.instruments import get_class_groups

        load_start = data.start - timedelta(days=self.lookback_window + 60)
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
        raw_z = _rolling_zscore_panel(log_prices, window=self.lookback_window)
        signal_raw = np.tanh(-raw_z.clip(lower=-3.0, upper=3.0))

        sub_class_groups = get_class_groups(
            session, data.instrument_ids, column=self.class_column
        )

        return _materialise_value_outputs(
            signal_raw=signal_raw,
            log_prices=log_prices,
            data=data,
            sub_class_groups=sub_class_groups,
        )


# ----------------------------------------------------------------------
# Materialisation
# ----------------------------------------------------------------------
def _materialise_value_outputs(
    *,
    signal_raw: pd.DataFrame,
    log_prices: pd.DataFrame,
    data: SignalInput,
    sub_class_groups: dict[str, list[str]] | None,
) -> list[SignalOutput]:
    start = pd.Timestamp(data.start).tz_convert("UTC")
    end = pd.Timestamp(data.end).tz_convert("UTC")
    mask = (signal_raw.index >= start) & (signal_raw.index <= end)
    if not mask.any():
        return []

    returns = log_prices.diff()
    rolling_per_inst = {
        col: rolling_sharpe(signal_raw[col], returns[col], window=252, min_periods=60)
        for col in signal_raw.columns
    }
    zscored = {
        col: rolling_zscore_of_self(signal_raw[col], lookback=252, min_periods=60)
        for col in signal_raw.columns
    }

    outputs: list[SignalOutput] = []
    for value_ts in signal_raw.index[mask]:
        row_values: dict[str, float] = {}
        for col in signal_raw.columns:
            v = signal_raw.at[value_ts, col]
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            row_values[col] = float(v)
        if not row_values:
            continue
        ranks = cross_sectional_rank(row_values, sub_class_groups=sub_class_groups)
        for col, v in row_values.items():
            z_val = zscored[col].get(value_ts)
            z = float(z_val) if z_val is not None and not np.isnan(z_val) else 0.0
            rs = rolling_per_inst[col].get(value_ts)
            rs_v = float(rs) if rs is not None and not np.isnan(rs) else None
            outputs.append(
                SignalOutput(
                    instrument_id=col,
                    value_ts=value_ts.to_pydatetime(),
                    observation_ts=data.as_of,
                    raw_value=v,
                    zscore=z,
                    rank=float(ranks.get(col, 0.5)),
                    confidence=float(
                        _confidence_from_completeness(log_prices[col], value_ts, lookback=252)
                    ),
                    rolling_sharpe_252=rs_v,
                    metadata={
                        "sub_class_ranked": sub_class_groups is not None,
                    },
                )
            )
    return outputs


__all__ = ["CrossSectionalValue", "ZScoreValue"]
