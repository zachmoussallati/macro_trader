"""Carry spot-proxy placeholder.

PLACEHOLDER — proper carry requires futures curve data, arriving in
Stage 12. Until then, this module produces low-confidence proxies for
compatibility with the signal framework. For most commodities it emits
zero with confidence=0; for a handful (oil, gold) where FRED publishes a
spot price next to a near-term forward proxy we emit a small carry
estimate with confidence ~0.3.

The composite scoring (Stage 7) weights every signal by its confidence,
so low-confidence carry is effectively excluded from portfolio decisions
until proper data is available.
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


# Instruments where we have *some* proxy basis. The actual carry value
# is still degenerate until Stage 12.
PROXY_CAPABLE = ("CL", "BZ", "GC", "NG")


class CarrySpotProxy(SignalMethod):
    metadata = MethodMetadata(
        method_id="carry.spot_proxy.v1",
        component="carry_signal",
        name="Carry (spot-proxy placeholder)",
        version="1.0.0",
        description=(
            "PLACEHOLDER — proper carry needs a futures curve (Stage 12). "
            "Emits zero with confidence=0 for unsupported instruments; "
            "low-confidence proxy for the handful where a near-term forward "
            "is approximable from FRED."
        ),
        references=[],
    )

    def __init__(
        self,
        *,
        proxy_capable: tuple[str, ...] = PROXY_CAPABLE,
        proxy_confidence: float = 0.3,
        zero_confidence: float = 0.0,
        smoothing_window: int = 20,
    ) -> None:
        self.proxy_capable = tuple(proxy_capable)
        self.proxy_confidence = float(proxy_confidence)
        self.zero_confidence = float(zero_confidence)
        self.smoothing_window = int(smoothing_window)

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("CarrySpotProxy requires a DB session")

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

        # Proxy carry: smooth log-return, then take the negative of the
        # rolling drift so positive = backwardation-like (a strong
        # short-term drift relative to the long-term level is the closest
        # ETF-proxy approximation we can make without futures data).
        log_ret = np.log(panel.replace(0, np.nan)).diff()
        smoothed = log_ret.rolling(window=self.smoothing_window, min_periods=10).mean()
        raw = np.tanh(-smoothed * 50.0)  # squash; sign convention noted above

        # Restrict to the requested output window.
        start = pd.Timestamp(data.start).tz_convert("UTC")
        end = pd.Timestamp(data.end).tz_convert("UTC")
        mask = (raw.index >= start) & (raw.index <= end)
        if not mask.any():
            return []

        # Per-instrument zero out non-proxy capable. Keep them in the output
        # with confidence=0 so the ranking / dashboard always sees the full
        # universe.
        for col in raw.columns:
            if col not in self.proxy_capable:
                raw[col] = 0.0

        returns = log_ret
        rolling_per_inst = {
            col: rolling_sharpe(raw[col], returns[col], window=252, min_periods=60)
            for col in raw.columns
        }
        zscored = {
            col: rolling_zscore_of_self(raw[col], lookback=252, min_periods=60)
            for col in raw.columns
        }

        outputs: list[SignalOutput] = []
        for value_ts in raw.index[mask]:
            row_values: dict[str, float] = {}
            for col in raw.columns:
                v = raw.at[value_ts, col]
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    continue
                row_values[col] = float(v)
            if not row_values:
                continue
            ranks = cross_sectional_rank(row_values)
            for col, v in row_values.items():
                z_val = zscored[col].get(value_ts)
                z = float(z_val) if z_val is not None and not np.isnan(z_val) else 0.0
                rs = rolling_per_inst[col].get(value_ts)
                rs_v = float(rs) if rs is not None and not np.isnan(rs) else None
                confidence = (
                    self.proxy_confidence if col in self.proxy_capable else self.zero_confidence
                )
                outputs.append(
                    SignalOutput(
                        instrument_id=col,
                        value_ts=value_ts.to_pydatetime(),
                        observation_ts=data.as_of,
                        raw_value=v,
                        zscore=z,
                        rank=float(ranks.get(col, 0.5)),
                        confidence=confidence,
                        rolling_sharpe_252=rs_v,
                        metadata={
                            "placeholder": True,
                            "proxy_capable": col in self.proxy_capable,
                        },
                    )
                )
        return outputs


__all__ = ["CarrySpotProxy"]
