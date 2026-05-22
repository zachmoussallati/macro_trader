"""Composite score comparator.

The most consequential comparator in the system: composite scores
drive every downstream decision (Stage 8 portfolio sizing, Stage 9
backtester). Two composite methods that *disagree* on the top-N
long ideas are producing materially different trade lists; that's
what ``top_5_overlap`` measures.

Metrics computed per ``compare()`` run:

- ``n_observations`` — count of (instrument, value_ts) rows in
  the intersection.
- ``direction_agreement`` — fraction of rows where
  ``sign(score_a) == sign(score_b)``.
- ``rank_correlation`` — Spearman correlation of cross-sectional
  ranks per value_ts, averaged across days.
- ``value_correlation`` — Pearson correlation of raw scores.
- ``top_5_overlap`` — fraction of method A's top-5 longs that
  appear in method B's top-5 longs (averaged across days).
- ``bottom_5_overlap`` — same for shorts.
- ``confidence_a`` / ``confidence_b`` — mean confidence reported
  by each method.
- ``n_signals_used_a_median`` / ``n_signals_used_b_median`` —
  median count of contributing signals per row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from macro_trader.composite.methods import CompositeInput, CompositeOutput
from macro_trader.methods.comparator import MethodComparator

if TYPE_CHECKING:
    pass


def _outputs_to_dataframe(outputs: list[CompositeOutput]) -> pd.DataFrame:
    if not outputs:
        return pd.DataFrame(
            columns=[
                "instrument_id",
                "value_ts",
                "score",
                "raw_score",
                "confidence",
                "n_signals_used",
            ]
        )
    rows = [
        {
            "instrument_id": o.instrument_id,
            "value_ts": pd.Timestamp(o.value_ts),
            "score": float(o.score),
            "raw_score": float(o.raw_score),
            "confidence": float(o.confidence),
            "n_signals_used": int(o.n_signals_used),
        }
        for o in outputs
    ]
    return pd.DataFrame(rows).set_index(["instrument_id", "value_ts"])


def _top_n_overlap(
    df_a: pd.DataFrame, df_b: pd.DataFrame, *, n: int = 5, bottom: bool = False
) -> float:
    """Per value_ts, take method A's top-n (or bottom-n) instruments
    by ``score`` and measure overlap with method B's top-n.

    Average across days. Returns NaN if there are no qualifying days
    (insufficient instruments per day)."""
    common_ts = df_a.index.get_level_values("value_ts").intersection(
        df_b.index.get_level_values("value_ts")
    )
    if len(common_ts) == 0:
        return float("nan")
    overlaps: list[float] = []
    for ts in common_ts.unique():
        a_slice = df_a.xs(ts, level="value_ts")["score"]
        b_slice = df_b.xs(ts, level="value_ts")["score"]
        if len(a_slice) < n or len(b_slice) < n:
            continue
        if bottom:
            top_a = set(a_slice.nsmallest(n).index)
            top_b = set(b_slice.nsmallest(n).index)
        else:
            top_a = set(a_slice.nlargest(n).index)
            top_b = set(b_slice.nlargest(n).index)
        denom = max(len(top_a), 1)
        overlaps.append(len(top_a & top_b) / denom)
    if not overlaps:
        return float("nan")
    return float(np.mean(overlaps))


def _rank_correlation_per_day(df_a: pd.DataFrame, df_b: pd.DataFrame) -> float:
    """Average daily cross-sectional Spearman correlation."""
    common_ts = df_a.index.get_level_values("value_ts").intersection(
        df_b.index.get_level_values("value_ts")
    )
    if len(common_ts) == 0:
        return float("nan")
    corrs: list[float] = []
    for ts in common_ts.unique():
        a_slice = df_a.xs(ts, level="value_ts")["score"]
        b_slice = df_b.xs(ts, level="value_ts")["score"]
        both = a_slice.to_frame("a").join(b_slice.to_frame("b"), how="inner")
        if len(both) < 3 or both["a"].nunique() < 2 or both["b"].nunique() < 2:
            continue
        c = both["a"].corr(both["b"], method="spearman")
        if c is not None and not pd.isna(c):
            corrs.append(float(c))
    if not corrs:
        return float("nan")
    return float(np.mean(corrs))


class CompositeComparator(
    MethodComparator[CompositeInput, list[CompositeOutput]]
):
    def __init__(self) -> None:
        super().__init__(component="composite_score")

    def _compute_metrics(
        self,
        output_a: list[CompositeOutput],
        output_b: list[CompositeOutput],
        *,
        data: CompositeInput,
    ) -> dict[str, float]:
        df_a = _outputs_to_dataframe(output_a)
        df_b = _outputs_to_dataframe(output_b)
        if df_a.empty or df_b.empty:
            return {
                "n_observations": 0.0,
                "n_observations_a": float(len(df_a)),
                "n_observations_b": float(len(df_b)),
            }

        merged = df_a.join(df_b, lsuffix="_a", rsuffix="_b", how="inner")
        n = len(merged)
        if n == 0:
            return {
                "n_observations": 0.0,
                "n_observations_a": float(len(df_a)),
                "n_observations_b": float(len(df_b)),
            }
        sign_a = np.sign(merged["score_a"].fillna(0.0))
        sign_b = np.sign(merged["score_b"].fillna(0.0))
        direction_agreement = float((sign_a == sign_b).mean())

        value_corr = (
            float(merged["score_a"].corr(merged["score_b"]))
            if merged["score_a"].nunique() > 1 and merged["score_b"].nunique() > 1
            else float("nan")
        )

        return {
            "n_observations": float(n),
            "direction_agreement": direction_agreement,
            "rank_correlation": _rank_correlation_per_day(df_a, df_b),
            "value_correlation": value_corr,
            "top_5_overlap": _top_n_overlap(df_a, df_b, n=5),
            "bottom_5_overlap": _top_n_overlap(df_a, df_b, n=5, bottom=True),
            "confidence_a": float(merged["confidence_a"].mean()),
            "confidence_b": float(merged["confidence_b"].mean()),
            "n_signals_used_a_median": float(merged["n_signals_used_a"].median()),
            "n_signals_used_b_median": float(merged["n_signals_used_b"].median()),
        }

    def _compute_agreement(
        self,
        output_a: list[CompositeOutput],
        output_b: list[CompositeOutput],
    ) -> dict[str, float]:
        df_a = _outputs_to_dataframe(output_a)
        df_b = _outputs_to_dataframe(output_b)
        if df_a.empty or df_b.empty:
            return {}
        merged = df_a.join(df_b, lsuffix="_a", rsuffix="_b", how="inner")
        if merged.empty:
            return {}
        return {
            "direction_agreement": float(
                (
                    np.sign(merged["score_a"].fillna(0.0))
                    == np.sign(merged["score_b"].fillna(0.0))
                ).mean()
            ),
            "top_5_overlap": _top_n_overlap(df_a, df_b, n=5),
        }

    def _compute_stability(
        self,
        method_a: object,
        method_b: object,
        data: CompositeInput,
    ) -> dict[str, float]:
        # Composite methods don't accept the perturbation pattern the
        # base class uses; skip — comparator runs per period and
        # period-over-period stability is queryable from the
        # historical comparisons table instead.
        return {}


__all__ = ["CompositeComparator", "_top_n_overlap"]
