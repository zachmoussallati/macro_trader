"""Regime-conditional weight derivation from
``regime.regime_attribution``.

The load-bearing piece of Stage 7. Composite methods read these
weights (frozen as snapshots in ``signals.composite_weights``) and
combine them with regime probabilities to produce per-(instrument,
signal) effective weights.

Algorithm:

1. Pull attribution rows for the trailing ``lookback_days`` from
   ``regime.regime_attribution``, filtered to one regime method.
2. For each (regime_label, signal_method_id) bucket:
   - If ``n_observations >= min_observations`` and ``sharpe`` is
     non-null: raw_weight = ``max(0, sharpe)``.
   - Else: fall back to ``1.0`` (flat prior).
3. Normalise per-regime: weights within a single ``regime_label``
   sum to 1.0.
4. EWM-smooth against the previous snapshot (``alpha=0.3`` default)
   to avoid weights jumping week-to-week.

Output rows carry ``weight_source ∈ {'attribution_sharpe', 'prior',
'smoothed'}`` for downstream attribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.db.models.regime import RegimeAttribution
from macro_trader.db.models.signals import CompositeWeight
from macro_trader.db.models.system import MethodRegistryRow
from macro_trader.logging_setup import get_logger
from macro_trader.regime import NAMED_REGIMES

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


@dataclass(slots=True)
class WeightRow:
    """One persisted row of the composite weight snapshot."""

    method_id: str
    snapshot_ts: datetime
    regime_label: str
    signal_method_id: str
    weight: float
    weight_source: str
    weight_metadata: dict[str, object]


def _load_attribution(
    session: Session,
    *,
    regime_method_id: str,
    as_of: datetime,
    lookback_days: int,
) -> pd.DataFrame:
    start = as_of - timedelta(days=lookback_days)
    rows = list(
        session.scalars(
            select(RegimeAttribution)
            .where(RegimeAttribution.regime_method_id == regime_method_id)
            .where(RegimeAttribution.value_ts >= start)
            .where(RegimeAttribution.value_ts <= as_of)
        )
    )
    if not rows:
        return pd.DataFrame(
            columns=[
                "regime_label",
                "signal_method_id",
                "n_observations",
                "sharpe",
                "value_ts",
            ]
        )
    return pd.DataFrame(
        [
            {
                "regime_label": r.regime_label,
                "signal_method_id": r.signal_method_id,
                "n_observations": int(r.n_observations),
                "sharpe": float(r.sharpe) if r.sharpe is not None else float("nan"),
                "value_ts": r.value_ts,
            }
            for r in rows
        ]
    )


def _load_signal_method_ids(session: Session) -> list[str]:
    """All registered signal methods (component ends in ``_signal``).

    Used as the union of methods that *could* show up in attribution;
    weights default to the flat prior for methods that don't have an
    attribution bucket yet.
    """
    rows = list(
        session.scalars(
            select(MethodRegistryRow).where(
                MethodRegistryRow.component.like("%_signal")
            )
        )
    )
    return [r.method_id for r in rows]


def _load_previous_snapshot(
    session: Session,
    *,
    composite_method_id: str,
    as_of: datetime,
) -> pd.DataFrame:
    """Most-recent prior snapshot for EWM smoothing (returns empty
    if none exist)."""
    latest_ts = session.scalar(
        select(CompositeWeight.snapshot_ts)
        .where(CompositeWeight.method_id == composite_method_id)
        .where(CompositeWeight.snapshot_ts < as_of)
        .order_by(CompositeWeight.snapshot_ts.desc())
        .limit(1)
    )
    if latest_ts is None:
        return pd.DataFrame(
            columns=["regime_label", "signal_method_id", "weight"]
        )
    rows = list(
        session.scalars(
            select(CompositeWeight)
            .where(CompositeWeight.method_id == composite_method_id)
            .where(CompositeWeight.snapshot_ts == latest_ts)
        )
    )
    return pd.DataFrame(
        [
            {
                "regime_label": r.regime_label,
                "signal_method_id": r.signal_method_id,
                "weight": float(r.weight),
            }
            for r in rows
        ]
    )


def compute_regime_conditional_weights(
    session: Session,
    *,
    composite_method_id: str = "composite.linear.v1",
    regime_method_id: str = "regime.rules.v1",
    as_of: datetime,
    lookback_days: int = 252,
    min_observations: int = 20,
    smoothing_alpha: float = 0.3,
    weight_floor: float = 0.0,
) -> list[WeightRow]:
    """Compute the per-(regime, signal) weight table for a snapshot.

    Returns the in-memory list; persistence is the caller's job
    via :func:`persist_weight_snapshot` so callers can inspect the
    rows before commit if needed.
    """
    attribution = _load_attribution(
        session,
        regime_method_id=regime_method_id,
        as_of=as_of,
        lookback_days=lookback_days,
    )
    signal_method_ids = _load_signal_method_ids(session)
    if not signal_method_ids:
        log.info("composite.weights.no_signal_methods")
        return []

    # For each (regime, signal) bucket, derive a raw weight.
    rows: list[dict[str, object]] = []
    for regime_label in NAMED_REGIMES:
        for signal_method_id in signal_method_ids:
            bucket = attribution[
                (attribution["regime_label"] == regime_label)
                & (attribution["signal_method_id"] == signal_method_id)
            ]
            if bucket.empty:
                rows.append(
                    {
                        "regime_label": regime_label,
                        "signal_method_id": signal_method_id,
                        "raw_weight": 1.0,
                        "weight_source": "prior",
                        "n_observations": 0,
                        "sharpe": None,
                    }
                )
                continue
            # Use the most-recent attribution row per bucket (the
            # weekly refit overwrites; we take the latest).
            latest = bucket.sort_values("value_ts").iloc[-1]
            n_obs = int(latest["n_observations"])
            sharpe = float(latest["sharpe"]) if pd.notna(latest["sharpe"]) else None
            if n_obs >= min_observations and sharpe is not None:
                raw = max(float(weight_floor), sharpe)
                source = "attribution_sharpe"
            else:
                raw = 1.0
                source = "prior"
            rows.append(
                {
                    "regime_label": regime_label,
                    "signal_method_id": signal_method_id,
                    "raw_weight": raw,
                    "weight_source": source,
                    "n_observations": n_obs,
                    "sharpe": sharpe,
                }
            )

    df = pd.DataFrame(rows)
    # Per-regime normalisation.
    df["normalised_weight"] = 0.0
    for _regime_label, group in df.groupby("regime_label"):
        total = float(group["raw_weight"].sum())
        if total <= 0:
            # All-zero (every sharpe was negative + clamped to floor=0)
            # — fall back to flat priors for that regime.
            df.loc[group.index, "normalised_weight"] = 1.0 / max(len(group), 1)
            df.loc[group.index, "weight_source"] = "prior"
        else:
            df.loc[group.index, "normalised_weight"] = (
                group["raw_weight"] / total
            )

    # EWM smoothing against the prior snapshot.
    previous = _load_previous_snapshot(
        session,
        composite_method_id=composite_method_id,
        as_of=as_of,
    )
    if not previous.empty and smoothing_alpha > 0:
        # Per-(regime, signal) merge; missing prior weights stay at
        # the new weight (no smoothing for first appearance).
        prev_idx = previous.set_index(["regime_label", "signal_method_id"])[
            "weight"
        ]
        smoothed: list[float] = []
        smoothed_sources: list[str] = []
        for _, r in df.iterrows():
            key = (r["regime_label"], r["signal_method_id"])
            new_w = float(r["normalised_weight"])
            prev_w = (
                float(prev_idx.loc[key]) if key in prev_idx.index else None
            )
            if prev_w is None:
                smoothed.append(new_w)
                smoothed_sources.append(str(r["weight_source"]))
            else:
                blended = smoothing_alpha * new_w + (1.0 - smoothing_alpha) * prev_w
                smoothed.append(float(blended))
                smoothed_sources.append("smoothed")
        df["final_weight"] = smoothed
        df["weight_source"] = smoothed_sources

        # Re-normalise after smoothing (EWM doesn't guarantee per-regime
        # sums stay at 1).
        for _regime_label, group in df.groupby("regime_label"):
            total = float(group["final_weight"].sum())
            if total > 0:
                df.loc[group.index, "final_weight"] = group["final_weight"] / total
    else:
        df["final_weight"] = df["normalised_weight"]

    out: list[WeightRow] = []
    for _, r in df.iterrows():
        meta: dict[str, object] = {
            "n_observations": int(r["n_observations"]),
            "raw_weight": float(r["raw_weight"]),
            "normalised_weight": float(r["normalised_weight"]),
        }
        if r["sharpe"] is not None and pd.notna(r["sharpe"]):
            meta["sharpe"] = float(r["sharpe"])
        out.append(
            WeightRow(
                method_id=composite_method_id,
                snapshot_ts=as_of,
                regime_label=str(r["regime_label"]),
                signal_method_id=str(r["signal_method_id"]),
                weight=float(r["final_weight"]),
                weight_source=str(r["weight_source"]),
                weight_metadata=meta,
            )
        )
    return out


def persist_weight_snapshot(session: Session, rows: list[WeightRow]) -> int:
    """Insert the snapshot. The PK is (method, snapshot_ts, regime,
    signal) so re-running with the same snapshot_ts is an upsert."""
    if not rows:
        return 0
    payload = [
        {
            "method_id": r.method_id,
            "snapshot_ts": r.snapshot_ts,
            "regime_label": r.regime_label,
            "signal_method_id": r.signal_method_id,
            "weight": r.weight,
            "weight_source": r.weight_source,
            "weight_metadata": r.weight_metadata,
        }
        for r in rows
    ]
    stmt = pg_insert(CompositeWeight).values(payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=["method_id", "snapshot_ts", "regime_label", "signal_method_id"],
        set_={
            "weight": stmt.excluded.weight,
            "weight_source": stmt.excluded.weight_source,
            "weight_metadata": stmt.excluded.weight_metadata,
        },
    )
    session.execute(stmt)
    session.flush()
    return len(rows)


def load_latest_weight_snapshot(
    session: Session,
    *,
    composite_method_id: str,
    as_of: datetime,
) -> pd.DataFrame:
    """Read the most-recent weight snapshot at or before ``as_of``.

    Returns a DataFrame indexed by (regime_label, signal_method_id)
    with columns (weight, weight_source, snapshot_ts). Empty if the
    method has no snapshots yet.
    """
    latest_ts = session.scalar(
        select(CompositeWeight.snapshot_ts)
        .where(CompositeWeight.method_id == composite_method_id)
        .where(CompositeWeight.snapshot_ts <= as_of)
        .order_by(CompositeWeight.snapshot_ts.desc())
        .limit(1)
    )
    if latest_ts is None:
        return pd.DataFrame(
            columns=["regime_label", "signal_method_id", "weight", "weight_source", "snapshot_ts"]
        ).set_index(["regime_label", "signal_method_id"])
    rows = list(
        session.scalars(
            select(CompositeWeight)
            .where(CompositeWeight.method_id == composite_method_id)
            .where(CompositeWeight.snapshot_ts == latest_ts)
        )
    )
    df = pd.DataFrame(
        [
            {
                "regime_label": r.regime_label,
                "signal_method_id": r.signal_method_id,
                "weight": float(r.weight),
                "weight_source": r.weight_source,
                "snapshot_ts": r.snapshot_ts,
            }
            for r in rows
        ]
    )
    return df.set_index(["regime_label", "signal_method_id"])


def effective_weights_for_probabilities(
    weight_snapshot: pd.DataFrame,
    probability_vector: dict[str, float],
) -> dict[str, float]:
    """Collapse the (regime, signal) snapshot into per-signal weights
    using a probability vector.

    ``effective_weight_k = sum_r (prob_r * weight_r,k)``.

    Returns a dict keyed by ``signal_method_id`` -> effective weight.
    Empty dict if the snapshot is empty.
    """
    if weight_snapshot.empty:
        return {}
    out: dict[str, float] = {}
    for (regime_label, signal_method_id), row in weight_snapshot.iterrows():
        prob = float(probability_vector.get(regime_label, 0.0))
        if prob == 0.0:
            continue
        out[signal_method_id] = out.get(signal_method_id, 0.0) + prob * float(row["weight"])
    return out


__all__ = [
    "WeightRow",
    "compute_regime_conditional_weights",
    "effective_weights_for_probabilities",
    "load_latest_weight_snapshot",
    "persist_weight_snapshot",
]


_ = np  # numpy intentionally imported for downstream callers
