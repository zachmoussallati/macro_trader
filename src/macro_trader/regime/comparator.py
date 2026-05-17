"""Regime classifier comparator.

Unlike signal comparators which measure agreement on directional
signal values, regime comparators measure agreement on categorical
labels.

Key metrics:

- ``label_agreement`` — fraction of overlapping (value_ts) rows
  where both methods chose the same label.
- ``probability_correlation`` — mean Pearson correlation across
  per-regime probability series.
- ``transition_alignment`` — fraction of transition events shared
  within a +/-3-day window.
- ``rolling_label_stability`` — per-method: how often the
  classifier holds its label day-to-day. Low values indicate a
  jumpy classifier.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from macro_trader.methods.comparator import MethodComparator
from macro_trader.regime.methods import RegimeInput, RegimeOutput

if TYPE_CHECKING:
    pass


def _to_dataframe(outputs: list[RegimeOutput]) -> pd.DataFrame:
    if not outputs:
        return pd.DataFrame(
            columns=["value_ts", "label", "confidence", "probability_vector"]
        )
    rows = [
        {
            "value_ts": o.value_ts,
            "label": o.label,
            "confidence": o.confidence,
            "probability_vector": o.probability_vector,
        }
        for o in outputs
    ]
    return pd.DataFrame(rows).set_index("value_ts").sort_index()


def _label_stability(df: pd.DataFrame) -> float:
    if df.empty or len(df) < 2:
        return float("nan")
    same_as_prev = (df["label"].values[1:] == df["label"].values[:-1]).mean()
    return float(same_as_prev)


def _transition_dates(df: pd.DataFrame) -> set[pd.Timestamp]:
    if df.empty:
        return set()
    transitions = df.index[1:][df["label"].values[1:] != df["label"].values[:-1]]
    return {pd.Timestamp(ts).normalize() for ts in transitions}


def _transition_alignment(a: pd.DataFrame, b: pd.DataFrame, *, window_days: int = 3) -> float:
    trans_a = _transition_dates(a)
    trans_b = _transition_dates(b)
    if not trans_a or not trans_b:
        return float("nan")
    matched = 0
    for ta in trans_a:
        if any(abs((ta - tb).days) <= window_days for tb in trans_b):
            matched += 1
    return float(matched / max(len(trans_a), 1))


def _probability_correlation(a: pd.DataFrame, b: pd.DataFrame) -> float:
    common = a.index.intersection(b.index)
    if len(common) < 5:
        return float("nan")
    a_sub = a.loc[common]
    b_sub = b.loc[common]
    # Per-regime correlation: extract each regime's probability time
    # series and Pearson-correlate, then average.
    if not a_sub["probability_vector"].iloc[0]:
        return float("nan")
    regimes = list(a_sub["probability_vector"].iloc[0].keys())
    corrs = []
    for r in regimes:
        a_series = pd.Series(
            [float(pv.get(r, 0.0)) for pv in a_sub["probability_vector"]],
            index=common,
        )
        b_series = pd.Series(
            [float(pv.get(r, 0.0)) for pv in b_sub["probability_vector"]],
            index=common,
        )
        if a_series.std() == 0 or b_series.std() == 0:
            continue
        corrs.append(float(a_series.corr(b_series)))
    return float(np.mean(corrs)) if corrs else float("nan")


class RegimeClassifierComparator(
    MethodComparator[RegimeInput, list[RegimeOutput]]
):
    def __init__(self) -> None:
        super().__init__(component="regime_classifier")

    def _compute_metrics(
        self,
        output_a: list[RegimeOutput],
        output_b: list[RegimeOutput],
        *,
        data: RegimeInput,
    ) -> dict[str, float]:
        df_a = _to_dataframe(output_a)
        df_b = _to_dataframe(output_b)
        if df_a.empty or df_b.empty:
            return {
                "n_observations": 0.0,
                "label_agreement": float("nan"),
                "probability_correlation": float("nan"),
                "transition_alignment": float("nan"),
                "rolling_label_stability_a": float("nan"),
                "rolling_label_stability_b": float("nan"),
                "confidence_a": float("nan"),
                "confidence_b": float("nan"),
            }
        common = df_a.index.intersection(df_b.index)
        label_agreement = (
            float((df_a.loc[common, "label"] == df_b.loc[common, "label"]).mean())
            if len(common) > 0
            else float("nan")
        )
        return {
            "n_observations": float(len(common)),
            "label_agreement": label_agreement,
            "probability_correlation": _probability_correlation(df_a, df_b),
            "transition_alignment": _transition_alignment(df_a, df_b),
            "rolling_label_stability_a": _label_stability(df_a),
            "rolling_label_stability_b": _label_stability(df_b),
            "confidence_a": float(df_a["confidence"].mean()),
            "confidence_b": float(df_b["confidence"].mean()),
        }

    def _compute_agreement(
        self, output_a: list[RegimeOutput], output_b: list[RegimeOutput]
    ) -> dict[str, float]:
        # Override the default: signal-output correlation doesn't
        # apply to categorical labels. label_agreement already
        # serves the purpose; expose it under the agreement key too.
        df_a = _to_dataframe(output_a)
        df_b = _to_dataframe(output_b)
        if df_a.empty or df_b.empty:
            return {}
        common = df_a.index.intersection(df_b.index)
        if len(common) == 0:
            return {}
        return {
            "label_agreement": float(
                (df_a.loc[common, "label"] == df_b.loc[common, "label"]).mean()
            )
        }

    def _compute_stability(
        self,
        method_a: object,
        method_b: object,
        data: RegimeInput,
    ) -> dict[str, float]:
        # Skip the default signal-stability perturbation; regime
        # methods don't accept perturbed input the same way.
        return {}


__all__ = ["RegimeClassifierComparator"]
