"""Portfolio construction comparator.

Measures whether two portfolio methods produce similar sized
positions. Different from the composite comparator (which measures
signal-aggregation agreement): this measures sizing agreement.

Position sign agreement is the most important metric — methods can
differ on magnitude (that's expected) but should agree on direction
95%+ of the time. The ``top_3_long_overlap`` /
``top_3_short_overlap`` metrics measure agreement on the biggest
positions, which dominate portfolio risk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from macro_trader.methods.comparator import MethodComparator
from macro_trader.portfolio.construction.methods import (
    PortfolioInput,
    PortfolioOutput,
)

if TYPE_CHECKING:
    pass


def _output_to_dataframe(output: PortfolioOutput) -> pd.DataFrame:
    if not output.positions:
        return pd.DataFrame(
            columns=[
                "instrument_id",
                "target_weight",
                "expected_vol_contribution",
                "block",
            ]
        )
    rows = [
        {
            "instrument_id": p.instrument_id,
            "target_weight": float(p.target_weight),
            "expected_vol_contribution": float(p.expected_vol_contribution),
            "block": p.block,
        }
        for p in output.positions
    ]
    return pd.DataFrame(rows).set_index("instrument_id")


def _top_n_overlap_directional(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    *,
    n: int = 3,
    direction: str = "long",
) -> float:
    if df_a.empty or df_b.empty:
        return float("nan")
    if direction == "long":
        a_pool = df_a[df_a["target_weight"] > 0]
        b_pool = df_b[df_b["target_weight"] > 0]
        if a_pool.empty or b_pool.empty:
            return float("nan")
        top_a = set(a_pool["target_weight"].nlargest(n).index)
        top_b = set(b_pool["target_weight"].nlargest(n).index)
    else:
        a_pool = df_a[df_a["target_weight"] < 0]
        b_pool = df_b[df_b["target_weight"] < 0]
        if a_pool.empty or b_pool.empty:
            return float("nan")
        top_a = set(a_pool["target_weight"].nsmallest(n).index)
        top_b = set(b_pool["target_weight"].nsmallest(n).index)
    if not top_a or not top_b:
        return float("nan")
    return float(len(top_a & top_b) / max(len(top_a), 1))


def _block_exposure(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return {}
    out: dict[str, float] = {}
    for _, row in df.iterrows():
        block = str(row.get("block") or "unknown")
        out[block] = out.get(block, 0.0) + abs(float(row["target_weight"]))
    total = sum(out.values())
    if total > 0:
        out = {k: v / total for k, v in out.items()}
    return out


class PortfolioConstructionComparator(
    MethodComparator[PortfolioInput, PortfolioOutput]
):
    def __init__(self) -> None:
        super().__init__(component="portfolio_construction")

    def _compute_metrics(
        self,
        output_a: PortfolioOutput,
        output_b: PortfolioOutput,
        *,
        data: PortfolioInput,
    ) -> dict[str, float]:
        df_a = _output_to_dataframe(output_a)
        df_b = _output_to_dataframe(output_b)
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
        sign_a = np.sign(merged["target_weight_a"].fillna(0.0))
        sign_b = np.sign(merged["target_weight_b"].fillna(0.0))
        sign_agreement = float((sign_a == sign_b).mean())

        weight_corr = (
            float(merged["target_weight_a"].corr(merged["target_weight_b"]))
            if merged["target_weight_a"].nunique() > 1
            and merged["target_weight_b"].nunique() > 1
            else float("nan")
        )
        rank_corr = float(
            merged["target_weight_a"]
            .abs()
            .rank()
            .corr(merged["target_weight_b"].abs().rank())
        )
        l1 = float((merged["target_weight_a"] - merged["target_weight_b"]).abs().sum())

        return {
            "n_observations": float(n),
            "position_sign_agreement": sign_agreement,
            "weight_correlation": weight_corr,
            "rank_correlation": rank_corr,
            "weight_l1_distance": l1,
            "top_3_long_overlap": _top_n_overlap_directional(
                df_a, df_b, n=3, direction="long"
            ),
            "top_3_short_overlap": _top_n_overlap_directional(
                df_a, df_b, n=3, direction="short"
            ),
            "expected_vol_a": float(output_a.expected_portfolio_vol),
            "expected_vol_b": float(output_b.expected_portfolio_vol),
        }

    def _compute_agreement(
        self,
        output_a: PortfolioOutput,
        output_b: PortfolioOutput,
    ) -> dict[str, float]:
        df_a = _output_to_dataframe(output_a)
        df_b = _output_to_dataframe(output_b)
        if df_a.empty or df_b.empty:
            return {}
        merged = df_a.join(df_b, lsuffix="_a", rsuffix="_b", how="inner")
        if merged.empty:
            return {}
        return {
            "position_sign_agreement": float(
                (
                    np.sign(merged["target_weight_a"].fillna(0.0))
                    == np.sign(merged["target_weight_b"].fillna(0.0))
                ).mean()
            ),
            "top_3_long_overlap": _top_n_overlap_directional(
                df_a, df_b, n=3, direction="long"
            ),
        }

    def _compute_stability(
        self,
        method_a: object,
        method_b: object,
        data: PortfolioInput,
    ) -> dict[str, float]:
        return {}


__all__ = [
    "PortfolioConstructionComparator",
    "_top_n_overlap_directional",
]
