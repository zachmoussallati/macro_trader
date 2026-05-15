"""Positioning signal methods.

Both methods follow the convention established in Stage 3: positive
``raw_value`` = long bias. Net positioning is sign-inverted so that an
extreme long crowd produces a contrarian short signal.

The "freshness" flag in ``SignalOutput.metadata`` indicates whether the
underlying COT report for ``value_ts`` was newly published on this run
(i.e. ``publication_ts`` advanced) or whether the signal is repeating
yesterday's number because no new report has been seen. The Dagster
asset can use this for alerting; downstream consumers can use it for
weighting.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from macro_trader.data.loaders import load_cot_as_of
from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.output import cross_sectional_rank
from macro_trader.utils.dates import ensure_aware

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _net_positioning(df: pd.DataFrame, long_col: str, short_col: str) -> pd.Series:
    """Return (long - short) / open_interest as a fraction in [-1, 1].

    Open interest can be zero in degenerate (early-history) rows; we
    produce NaN for those rather than dividing through.
    """
    oi = df["open_interest"].replace(0, np.nan)
    return (df[long_col] - df[short_col]) / oi


def _rolling_zscore(series: pd.Series, *, window: int, min_periods: int) -> pd.Series:
    if series.empty:
        return series
    mu = series.rolling(window=window, min_periods=min_periods).mean()
    sigma = series.rolling(window=window, min_periods=min_periods).std(ddof=1).replace(
        0, np.nan
    )
    return (series - mu) / sigma


class _BaseCOTMethod(SignalMethod):
    """Shared scaffolding for the two COT-derived signal methods.

    Subclasses set:

    - ``LONG_COL`` / ``SHORT_COL``: the participant breakdown columns to
      read from the COT row (e.g. ``managed_money_long`` /
      ``managed_money_short`` for the baseline).
    - ``REPORT_TYPE``: ``"disaggregated"`` or ``"legacy"``.
    - ``METADATA``: the canonical :class:`MethodMetadata`.
    """

    LONG_COL: str
    SHORT_COL: str
    REPORT_TYPE: str

    def __init__(
        self,
        *,
        lookback_weeks: int = 156,
        min_history_weeks: int = 52,
        extreme_threshold: float = 2.0,
    ) -> None:
        self.lookback_weeks = int(lookback_weeks)
        self.min_history_weeks = int(min_history_weeks)
        self.extreme_threshold = float(extreme_threshold)

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError(f"{type(self).__name__} requires a DB session")

        # Window for emitting outputs.
        emit_start = pd.Timestamp(ensure_aware(data.start)).tz_convert("UTC")
        emit_end = pd.Timestamp(ensure_aware(data.end)).tz_convert("UTC")

        # Per-instrument net-positioning z-score series.
        per_instrument: dict[str, pd.Series] = {}
        publication_per_ts: dict[
            tuple[str, pd.Timestamp], pd.Timestamp
        ] = {}
        history_weeks: dict[str, int] = {}

        for instrument_id in data.instrument_ids:
            df = load_cot_as_of(
                session,
                instrument_id,
                as_of=data.as_of,
                report_type=self.REPORT_TYPE,
                lookback_weeks=self.lookback_weeks,
            )
            if df.empty:
                continue
            net = _net_positioning(df, self.LONG_COL, self.SHORT_COL)
            if net.dropna().empty:
                continue
            z = _rolling_zscore(
                net, window=self.lookback_weeks, min_periods=self.min_history_weeks
            )
            # Sign inversion: positive z (crowd net long) -> contrarian short.
            raw = -z.clip(lower=-3.0, upper=3.0).apply(np.tanh)
            per_instrument[instrument_id] = raw
            history_weeks[instrument_id] = int(net.dropna().shape[0])
            for ts in df.index:
                publication_per_ts[(instrument_id, ts)] = df.at[ts, "publication_ts"]

        if not per_instrument:
            return []

        # Emit one SignalOutput per (instrument, report_ts) inside the
        # requested window. Cross-sectional rank is computed per report_ts
        # across whatever instruments produced a value that week.
        # ``last_publication_seen`` lets us flag rows where the underlying
        # report was newly published on this run.
        all_ts = sorted(
            {
                ts
                for series in per_instrument.values()
                for ts in series.index
                if emit_start <= ts <= emit_end
            }
        )

        outputs: list[SignalOutput] = []
        for ts in all_ts:
            row_values: dict[str, float] = {}
            for inst, series in per_instrument.items():
                if ts not in series.index:
                    continue
                v = series.at[ts]
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    continue
                row_values[inst] = float(v)
            if not row_values:
                continue

            ranks = cross_sectional_rank(row_values)

            for inst, v in row_values.items():
                pub = publication_per_ts.get((inst, ts))
                pub_dt = pub.to_pydatetime() if isinstance(pub, pd.Timestamp) else None
                hw = history_weeks.get(inst, 0)
                conf = min(1.0, hw / float(self.lookback_weeks))

                # In-sample z is already produced; the "is_extreme" tag is
                # convenient for the dashboard.
                is_extreme = abs(v) >= float(np.tanh(self.extreme_threshold))

                # Freshness: did this run see a newer publication for this
                # instrument than the prior week? Compare to the previous
                # available report_ts: if pub_dt > previous-week's publication,
                # call it fresh.
                series = per_instrument[inst]
                prior_ts = series.index[series.index.searchsorted(ts) - 1] if series.index.searchsorted(ts) > 0 else None
                prior_pub = publication_per_ts.get((inst, prior_ts)) if prior_ts is not None else None
                if pub_dt is not None and prior_pub is not None and prior_ts is not None:
                    is_fresh_data = pd.Timestamp(pub_dt) > pd.Timestamp(prior_pub) + timedelta(days=1)
                elif pub_dt is not None:
                    is_fresh_data = True
                else:
                    is_fresh_data = False

                meta: dict[str, Any] = {
                    "report_type": self.REPORT_TYPE,
                    "is_extreme": bool(is_extreme),
                    "is_fresh_data": bool(is_fresh_data),
                    "history_weeks": hw,
                }
                if pub_dt is not None:
                    meta["publication_ts"] = pub_dt.isoformat()

                outputs.append(
                    SignalOutput(
                        instrument_id=inst,
                        value_ts=ts.to_pydatetime(),
                        observation_ts=data.as_of,
                        raw_value=v,
                        # raw values are tanh-squashed; pre-squash z is more
                        # interpretable but tanh keeps everything in [-1, 1].
                        zscore=float(np.arctanh(np.clip(v, -0.9999, 0.9999))),
                        rank=float(ranks.get(inst, 0.5)),
                        confidence=float(conf),
                        rolling_sharpe_252=None,
                        metadata=meta,
                    )
                )
        return outputs


class CotZScore(_BaseCOTMethod):
    """Managed-money net positioning z-score (disaggregated report).

    Convention: positive raw_value = long bias (crowd net short ->
    contrarian long).
    """

    LONG_COL = "managed_money_long"
    SHORT_COL = "managed_money_short"
    REPORT_TYPE = "disaggregated"

    metadata = MethodMetadata(
        method_id="positioning.cot_zscore.v1",
        component="positioning_signal",
        name="Managed Money Net Positioning Z-Score",
        version="1.0.0",
        description=(
            "3-year rolling z-score of managed-money net positioning from the "
            "CFTC disaggregated report, sign-inverted (extreme net long -> "
            "contrarian short)."
        ),
        references=[
            "CFTC Commitment of Traders disaggregated report",
            "Briese (2007) The Commitments of Traders Bible",
        ],
    )


class CotCommercial(_BaseCOTMethod):
    """Commercial-net-positioning extremes from the Legacy COT report.

    Commercials (producers/users/hedgers) carry a 'smart money' read in
    commodities literature: extreme commercial net long historically
    precedes price strength; extreme net short precedes weakness. Sign
    convention matches the baseline.
    """

    LONG_COL = "producer_long"
    SHORT_COL = "producer_short"
    REPORT_TYPE = "legacy"

    metadata = MethodMetadata(
        method_id="positioning.cot_commercial.v1",
        component="positioning_signal",
        name="Legacy Commercial Net Positioning Extremes",
        version="1.0.0",
        description=(
            "3-year rolling z-score of commercial net positioning from the "
            "CFTC legacy report. Commercials are hedgers; extremes flag "
            "supply/demand imbalances."
        ),
        references=[
            "CFTC Legacy report",
            "Sanders, Boris, Manfredo (2004) on COT signal value in agricultural markets",
        ],
    )
