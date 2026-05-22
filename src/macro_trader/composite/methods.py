"""Three composite scoring methods.

- ``composite.linear.v1`` — BASELINE. Linear sum of
  ``weight * z * confidence`` per signal, transition-multiplier
  dampened, tanh-squashed to ``[-1, 1]``.
- ``composite.bayesian_hier.v1`` — SHADOW. Three-level
  Normal-Inverse-Gamma hierarchy (global / per-regime /
  per-instrument). Weekly refit produces shrunk per-instrument-per-
  regime weight posteriors; daily compute applies them to the
  current signal panel.
- ``composite.gbm.v1`` — SHADOW, gated on lightgbm. Quarterly refit
  trains a regression of next-day log return on the (signals *
  regime probabilities * instrument one-hot) feature panel; daily
  compute predicts.

All three share the same input shape (:class:`CompositeInput`),
output shape (:class:`CompositeOutput`), and the same signal-panel
loader (:func:`_load_signal_panel`). Only the *weighting* step
differs.
"""

# Stat-ML convention uses uppercase X / W / V in linalg blocks —
# suppress N803 / N806 file-wide so numpy code stays readable.
# ruff: noqa: N803, N806

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self, cast

import numpy as np
import pandas as pd
from sqlalchemy import select

from macro_trader.composite.transition import (
    latest_changepoint_probability,
    transition_multiplier_from_probability,
)
from macro_trader.composite.weights import (
    effective_weights_for_probabilities,
    load_latest_weight_snapshot,
)
from macro_trader.db.models.regime import RegimeState
from macro_trader.db.models.signals import SignalValue
from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import Method, MethodMetadata
from macro_trader.regime import NAMED_REGIMES

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def _lightgbm_available() -> bool:
    try:
        import lightgbm  # noqa: F401
    except Exception:
        return False
    return True


# ----------------------------------------------------------------------
# Input / output shapes
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class CompositeInput:
    """Input contract for a composite method's daily compute."""

    instrument_ids: list[str]
    as_of: datetime
    start: datetime
    end: datetime
    # The regime classifier method id whose probability vector we
    # blend the snapshot weights against.
    regime_method_id: str = "regime.rules.v1"
    # BOCPD method id used for the transition multiplier (linear
    # method applies explicitly; Bayesian/GBM include the raw prob
    # as a feature instead).
    bocpd_method_id: str = "regime.bocpd.v1"
    # Designated signal methods to feed into the composite. One entry
    # per signal family; built from
    # ``signals.designated.resolve_id(component)`` in the runner.
    designated_signal_method_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CompositeOutput:
    """One composite-score row. Maps 1:1 to ``signals.composite_scores``."""

    instrument_id: str
    value_ts: datetime
    observation_ts: datetime
    raw_score: float
    score: float
    confidence: float
    regime_label: str | None
    n_signals_used: int
    metadata: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Shared loaders
# ----------------------------------------------------------------------
def _load_signal_panel(
    session: Session,
    *,
    signal_method_ids: list[str],
    instrument_ids: list[str],
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> pd.DataFrame:
    """Return a long-form DataFrame with columns:
    (value_ts, instrument_id, signal_method_id, z, confidence).

    Uses ``zscore`` if present; falls back to ``raw_value`` when
    z is null. Both methods filter to rows with
    ``observation_ts <= as_of`` for point-in-time correctness.
    """
    if not signal_method_ids or not instrument_ids:
        return pd.DataFrame(
            columns=["value_ts", "instrument_id", "signal_method_id", "z", "confidence"]
        )
    rows = list(
        session.scalars(
            select(SignalValue)
            .where(SignalValue.signal_id.in_(signal_method_ids))
            .where(SignalValue.instrument_id.in_(instrument_ids))
            .where(SignalValue.value_ts >= start)
            .where(SignalValue.value_ts <= end)
            .where(SignalValue.observation_ts <= as_of)
        )
    )
    if not rows:
        return pd.DataFrame(
            columns=["value_ts", "instrument_id", "signal_method_id", "z", "confidence"]
        )
    records: list[dict[str, object]] = []
    for r in rows:
        z = r.zscore if r.zscore is not None else r.raw_value
        if z is None:
            continue
        records.append(
            {
                "value_ts": r.value_ts,
                "instrument_id": r.instrument_id,
                "signal_method_id": r.signal_id,
                "z": float(z),
                "confidence": float(r.confidence) if r.confidence is not None else 0.5,
            }
        )
    return pd.DataFrame(records)


def _load_regime_panel(
    session: Session,
    *,
    regime_method_id: str,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> pd.DataFrame:
    """One row per value_ts with the regime label + probability vector
    (latest observation_ts at or before ``as_of`` for each value_ts)."""
    rows = list(
        session.scalars(
            select(RegimeState)
            .where(RegimeState.method_id == regime_method_id)
            .where(RegimeState.value_ts >= start)
            .where(RegimeState.value_ts <= end)
            .where(RegimeState.observation_ts <= as_of)
            .order_by(RegimeState.value_ts, RegimeState.observation_ts.desc())
        )
    )
    if not rows:
        return pd.DataFrame(columns=["value_ts", "label", "probability_vector"])
    seen: dict[datetime, dict[str, object]] = {}
    for r in rows:
        if r.value_ts in seen:
            continue
        seen[r.value_ts] = {
            "value_ts": r.value_ts,
            "label": r.label,
            "probability_vector": dict(r.probability_vector or {}),
        }
    return pd.DataFrame(list(seen.values()))


def _flat_probability_vector(label: str | None = None) -> dict[str, float]:
    if label is not None and label in NAMED_REGIMES:
        out = dict.fromkeys(NAMED_REGIMES, 0.0)
        out[label] = 1.0
        return out
    return dict.fromkeys(NAMED_REGIMES, 1.0 / len(NAMED_REGIMES))


# ----------------------------------------------------------------------
# Composite method base
# ----------------------------------------------------------------------
class CompositeMethod(Method[CompositeInput, list[CompositeOutput]]):
    """Common scaffolding for composite methods."""

    metadata: MethodMetadata

    def fit(self, data: CompositeInput) -> None:
        return None

    def predict(self, data: CompositeInput) -> list[CompositeOutput]:
        raise RuntimeError("use compute(data, session)")

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> Self:
        return cls()


# ----------------------------------------------------------------------
# Method 1: composite.linear.v1 — BASELINE
# ----------------------------------------------------------------------
class LinearComposite(CompositeMethod):
    metadata = MethodMetadata(
        method_id="composite.linear.v1",
        component="composite_score",
        name="Linear Composite Score",
        version="1.0.0",
        description=(
            "Regime-weighted linear sum of z-scored signals * per-row "
            "confidence * BOCPD transition-multiplier. Tanh-squashed to "
            "[-1, 1]. Designated as the production composite."
        ),
        references=[],
    )

    def __init__(
        self,
        *,
        clip_z: float = 3.0,
        min_signals_for_score: int = 5,
        transition_threshold: float = 0.5,
        transition_floor: float = 0.5,
    ) -> None:
        self.clip_z = float(clip_z)
        self.min_signals_for_score = int(min_signals_for_score)
        self.transition_threshold = float(transition_threshold)
        self.transition_floor = float(transition_floor)

    def compute(
        self, data: CompositeInput, session: Session | None
    ) -> list[CompositeOutput]:
        if session is None:
            raise ValueError("LinearComposite requires a DB session")

        weight_snapshot = load_latest_weight_snapshot(
            session,
            composite_method_id=self.metadata.method_id,
            as_of=data.as_of,
        )
        signal_panel = _load_signal_panel(
            session,
            signal_method_ids=data.designated_signal_method_ids,
            instrument_ids=data.instrument_ids,
            start=data.start,
            end=data.end,
            as_of=data.as_of,
        )
        if signal_panel.empty:
            return []
        regime_panel = _load_regime_panel(
            session,
            regime_method_id=data.regime_method_id,
            start=data.start,
            end=data.end,
            as_of=data.as_of,
        )
        regime_by_ts: dict[datetime, dict[str, object]] = {
            row["value_ts"]: row for _, row in regime_panel.iterrows()
        }

        cp_prob = latest_changepoint_probability(
            session,
            bocpd_method_id=data.bocpd_method_id,
            as_of=data.as_of,
        )
        multiplier = transition_multiplier_from_probability(
            cp_prob,
            threshold=self.transition_threshold,
            floor=self.transition_floor,
        )

        outputs: list[CompositeOutput] = []
        # Group by (value_ts, instrument_id) to score one row at a time.
        grouped = signal_panel.groupby(["value_ts", "instrument_id"])
        for (value_ts, instrument_id), group in grouped:
            regime_info = regime_by_ts.get(value_ts)
            if regime_info is not None:
                prob_vec = cast(dict[str, float], regime_info["probability_vector"])
                label = str(regime_info["label"])
            else:
                prob_vec = _flat_probability_vector()
                label = None

            effective = (
                effective_weights_for_probabilities(weight_snapshot, prob_vec)
                if not weight_snapshot.empty
                else None
            )

            contributions: list[tuple[str, float, float, float, float]] = []
            for _, r in group.iterrows():
                sid = str(r["signal_method_id"])
                z = float(r["z"])
                conf = float(r["confidence"])
                if effective is not None:
                    w = float(effective.get(sid, 0.0))
                else:
                    # No snapshot yet — equal weight across the signals
                    # actually present this row.
                    w = 1.0 / max(len(group), 1)
                contrib = w * z * conf
                contributions.append((sid, w, z, conf, contrib))

            if len(contributions) < self.min_signals_for_score:
                continue

            raw_score = float(sum(c[4] for c in contributions))
            clipped = max(-self.clip_z, min(self.clip_z, raw_score))
            score = float(np.tanh(clipped)) * multiplier

            confidences = [c[3] for c in contributions]
            confidence = float(np.median(confidences))

            metadata = {
                "method": self.metadata.method_id,
                "transition_multiplier": multiplier,
                "transition_probability": (
                    float(cp_prob) if cp_prob is not None else None
                ),
                "regime_probability_vector": prob_vec,
                "contributions": [
                    {
                        "signal_method_id": sid,
                        "weight": w,
                        "z": z,
                        "confidence": conf,
                        "contribution": contrib,
                    }
                    for (sid, w, z, conf, contrib) in contributions
                ],
            }

            outputs.append(
                CompositeOutput(
                    instrument_id=str(instrument_id),
                    value_ts=value_ts,
                    observation_ts=data.as_of,
                    raw_score=raw_score,
                    score=score,
                    confidence=confidence,
                    regime_label=label,
                    n_signals_used=len(contributions),
                    metadata=metadata,
                )
            )
        return outputs


# ----------------------------------------------------------------------
# Method 2: composite.bayesian_hier.v1 — SHADOW
# ----------------------------------------------------------------------
class BayesianHierarchicalComposite(CompositeMethod):
    metadata = MethodMetadata(
        method_id="composite.bayesian_hier.v1",
        component="composite_score",
        name="Bayesian Hierarchical Composite",
        version="1.0.0",
        description=(
            "Three-level Normal-Inverse-Gamma hierarchy "
            "(global / per-regime / per-instrument) with closed-form "
            "shrinkage. Per-instrument weights shrink to per-regime; "
            "per-regime shrinks to global priors derived from "
            "attribution Sharpe."
        ),
        references=[
            "Gelman et al. (2013) Bayesian Data Analysis, Ch. 5",
            "Stage 5 nowcasting.bvar pattern (Normal-Inverse-Gamma)",
        ],
    )

    def __init__(
        self,
        *,
        lookback_days: int = 504,
        regime_shrinkage: float = 0.3,
        instrument_shrinkage: float = 0.5,
        clip_z: float = 3.0,
        min_signals_for_score: int = 5,
    ) -> None:
        self.lookback_days = int(lookback_days)
        self.regime_shrinkage = float(regime_shrinkage)
        self.instrument_shrinkage = float(instrument_shrinkage)
        self.clip_z = float(clip_z)
        self.min_signals_for_score = int(min_signals_for_score)
        # Fitted state: dict of (regime_label, instrument_id) -> {signal_method_id: weight}
        self._state: dict[str, Any] | None = None

    def fit(self, data: CompositeInput) -> None:
        # The actual fit happens in fit_from_history (called by the
        # weekly refit job). The Method.fit() interface only takes a
        # CompositeInput, which doesn't carry the historical data we
        # need; we leave it as a placeholder and rely on the refit
        # job calling fit_from_history directly.
        return None

    def fit_from_history(
        self,
        *,
        signal_panel: pd.DataFrame,
        regime_panel: pd.DataFrame,
        returns_panel: pd.DataFrame,
    ) -> None:
        """Fit the three-level hierarchy from historical data.

        Inputs:
            signal_panel: long-form (value_ts, instrument_id,
                signal_method_id, z, confidence) over the lookback.
            regime_panel: (value_ts, label, probability_vector).
            returns_panel: (value_ts, instrument_id, next_log_ret).
        """
        if signal_panel.empty or regime_panel.empty or returns_panel.empty:
            self._state = None
            return

        # Wide pivot: rows are (value_ts, instrument_id), columns are
        # signal_method_id, values = z * confidence (the contribution
        # the linear method would compute).
        sp = signal_panel.copy()
        sp["x"] = sp["z"] * sp["confidence"]
        wide = (
            sp.pivot_table(
                index=["value_ts", "instrument_id"],
                columns="signal_method_id",
                values="x",
                aggfunc="last",
            )
            .fillna(0.0)
        )
        if wide.empty:
            self._state = None
            return

        # Attach regime label per value_ts.
        regime_by_ts: dict[datetime, str] = {
            row["value_ts"]: str(row["label"]) for _, row in regime_panel.iterrows()
        }
        wide_idx = wide.index.get_level_values("value_ts")
        regime_labels = [regime_by_ts.get(ts) for ts in wide_idx]

        # Returns join.
        ret_by_key: dict[tuple[datetime, str], float] = {
            (row["value_ts"], row["instrument_id"]): float(row["next_log_ret"])
            for _, row in returns_panel.iterrows()
        }
        y_values: list[float] = []
        keep_idx: list[int] = []
        for i, (ts, inst) in enumerate(wide.index):
            r = ret_by_key.get((ts, inst))
            if r is None or not np.isfinite(r):
                continue
            y_values.append(r)
            keep_idx.append(i)
        if not keep_idx:
            self._state = None
            return

        X = wide.values[keep_idx]
        y = np.array(y_values, dtype=float)
        signal_cols = list(wide.columns)
        regime_labels = [regime_labels[i] for i in keep_idx]

        # Global OLS as the global prior centre.
        global_beta = self._ridge_solve(X, y, ridge=1.0)

        # Per-regime fits with shrinkage to global.
        regime_betas: dict[str, np.ndarray] = {}
        for regime in NAMED_REGIMES:
            mask = np.array([r == regime for r in regime_labels])
            if mask.sum() < max(20, len(signal_cols)):
                regime_betas[regime] = global_beta
                continue
            Xr = X[mask]
            yr = y[mask]
            beta_r = self._ridge_solve(Xr, yr, ridge=1.0)
            # Shrink to global by alpha.
            shrunk = (
                self.regime_shrinkage * global_beta
                + (1.0 - self.regime_shrinkage) * beta_r
            )
            regime_betas[regime] = shrunk

        # Per-(regime, instrument) fits, shrinking to per-regime.
        # Stored as dict[(regime, instrument)] -> dict[signal_method_id] -> weight.
        # Use the row instrument labels for the slice.
        wide_inst_idx = wide.index.get_level_values("instrument_id")
        instrument_labels = [wide_inst_idx[i] for i in keep_idx]
        instruments_seen = sorted(set(instrument_labels))

        per_inst_betas: dict[tuple[str, str], np.ndarray] = {}
        for regime in NAMED_REGIMES:
            regime_beta = regime_betas[regime]
            for inst in instruments_seen:
                mask = np.array(
                    [
                        rl == regime and il == inst
                        for rl, il in zip(regime_labels, instrument_labels, strict=False)
                    ]
                )
                if mask.sum() < max(10, len(signal_cols) // 2):
                    per_inst_betas[(regime, inst)] = regime_beta
                    continue
                Xri = X[mask]
                yri = y[mask]
                beta_ri = self._ridge_solve(Xri, yri, ridge=2.0)
                shrunk = (
                    self.instrument_shrinkage * regime_beta
                    + (1.0 - self.instrument_shrinkage) * beta_ri
                )
                per_inst_betas[(regime, inst)] = shrunk

        # Normalise per-(regime, instrument) so the weights act on z-scaled
        # signals comparably (clip to [-clip_z, clip_z] later). Convert
        # arrays back into {signal_method_id: weight} dicts for storage.
        weights_by_cell: dict[tuple[str, str], dict[str, float]] = {}
        for (regime, inst), beta in per_inst_betas.items():
            total = float(np.abs(beta).sum())
            normalised = (beta / total) if total > 0 else beta
            weights_by_cell[(regime, inst)] = {
                signal_cols[j]: float(normalised[j]) for j in range(len(signal_cols))
            }

        self._state = {
            "signal_columns": signal_cols,
            "global_beta": global_beta,
            "weights_by_cell": weights_by_cell,
            "fit_as_of": datetime.utcnow().isoformat(),
            "n_observations": len(y),
        }

    @staticmethod
    def _ridge_solve(X: np.ndarray, y: np.ndarray, *, ridge: float) -> np.ndarray:
        p = X.shape[1]
        XtX = X.T @ X + ridge * np.eye(p)
        Xty = X.T @ y
        return np.linalg.solve(XtX, Xty)

    def compute(
        self, data: CompositeInput, session: Session | None
    ) -> list[CompositeOutput]:
        if session is None:
            raise ValueError("BayesianHierarchicalComposite requires a DB session")
        if self._state is None:
            log.info("composite.bayesian_hier.no_state")
            return []

        signal_panel = _load_signal_panel(
            session,
            signal_method_ids=data.designated_signal_method_ids,
            instrument_ids=data.instrument_ids,
            start=data.start,
            end=data.end,
            as_of=data.as_of,
        )
        if signal_panel.empty:
            return []
        regime_panel = _load_regime_panel(
            session,
            regime_method_id=data.regime_method_id,
            start=data.start,
            end=data.end,
            as_of=data.as_of,
        )
        regime_by_ts: dict[datetime, dict[str, object]] = {
            row["value_ts"]: row for _, row in regime_panel.iterrows()
        }

        signal_cols: list[str] = self._state["signal_columns"]
        weights_by_cell: dict[tuple[str, str], dict[str, float]] = self._state[
            "weights_by_cell"
        ]
        cp_prob = latest_changepoint_probability(
            session,
            bocpd_method_id=data.bocpd_method_id,
            as_of=data.as_of,
        )

        outputs: list[CompositeOutput] = []
        for (value_ts, instrument_id), group in signal_panel.groupby(
            ["value_ts", "instrument_id"]
        ):
            regime_info = regime_by_ts.get(value_ts)
            if regime_info is not None:
                prob_vec = cast(dict[str, float], regime_info["probability_vector"])
                label = str(regime_info["label"])
            else:
                prob_vec = _flat_probability_vector()
                label = None

            # Probability-weighted blend across the per-(regime,
            # instrument) weight tables.
            blended: dict[str, float] = dict.fromkeys(signal_cols, 0.0)
            for regime in NAMED_REGIMES:
                prob = float(prob_vec.get(regime, 0.0))
                if prob == 0.0:
                    continue
                cell = weights_by_cell.get(
                    (regime, str(instrument_id))
                ) or weights_by_cell.get((regime, "_global_"))
                if cell is None:
                    continue
                for sid, w in cell.items():
                    blended[sid] = blended.get(sid, 0.0) + prob * w

            contributions: list[tuple[str, float, float, float, float]] = []
            for _, r in group.iterrows():
                sid = str(r["signal_method_id"])
                if sid not in blended:
                    continue
                z = float(r["z"])
                conf = float(r["confidence"])
                w = float(blended[sid])
                contrib = w * z * conf
                contributions.append((sid, w, z, conf, contrib))
            if len(contributions) < self.min_signals_for_score:
                continue

            raw_score = float(sum(c[4] for c in contributions))
            clipped = max(-self.clip_z, min(self.clip_z, raw_score))
            score = float(np.tanh(clipped))
            confidence = float(np.median([c[3] for c in contributions]))

            metadata = {
                "method": self.metadata.method_id,
                "transition_probability": (
                    float(cp_prob) if cp_prob is not None else None
                ),
                "regime_probability_vector": prob_vec,
                "contributions": [
                    {
                        "signal_method_id": sid,
                        "weight": w,
                        "z": z,
                        "confidence": conf,
                        "contribution": contrib,
                    }
                    for (sid, w, z, conf, contrib) in contributions
                ],
            }
            outputs.append(
                CompositeOutput(
                    instrument_id=str(instrument_id),
                    value_ts=value_ts,
                    observation_ts=data.as_of,
                    raw_score=raw_score,
                    score=score,
                    confidence=confidence,
                    regime_label=label,
                    n_signals_used=len(contributions),
                    metadata=metadata,
                )
            )
        return outputs

    def serialize(self) -> bytes:
        if self._state is None:
            return b""
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def deserialize(cls, blob: bytes) -> Self:
        m = cls()
        if blob:
            m._state = pickle.loads(blob)
        return m


# ----------------------------------------------------------------------
# Method 3: composite.gbm.v1 — SHADOW
# ----------------------------------------------------------------------
class GBMComposite(CompositeMethod):
    metadata = MethodMetadata(
        method_id="composite.gbm.v1",
        component="composite_score",
        name="GBM Composite Score",
        version="1.0.0",
        description=(
            "LightGBM regression on (signals * regime probabilities * "
            "instrument one-hot) features, trained quarterly to predict "
            "next-day log return. Predicts daily; output squashed to [-1, 1]."
        ),
        references=[
            "Friedman (2001) Greedy Function Approximation: A Gradient Boosting Machine",
        ],
    )

    def __init__(
        self,
        *,
        lookback_days: int = 1008,
        n_estimators: int = 500,
        learning_rate: float = 0.05,
        max_depth: int = 5,
        min_child_samples: int = 20,
        early_stopping_rounds: int = 30,
        validation_fold_fraction: float = 0.2,
        random_state: int = 42,
        clip_z: float = 3.0,
        min_signals_for_score: int = 5,
    ) -> None:
        if not _lightgbm_available():
            raise RuntimeError(
                "GBMComposite requires lightgbm; install via `uv sync --extra ml`"
            )
        self.lookback_days = int(lookback_days)
        self.n_estimators = int(n_estimators)
        self.learning_rate = float(learning_rate)
        self.max_depth = int(max_depth)
        self.min_child_samples = int(min_child_samples)
        self.early_stopping_rounds = int(early_stopping_rounds)
        self.validation_fold_fraction = float(validation_fold_fraction)
        self.random_state = int(random_state)
        self.clip_z = float(clip_z)
        self.min_signals_for_score = int(min_signals_for_score)
        self._state: dict[str, Any] | None = None

    def fit_from_history(
        self,
        *,
        signal_panel: pd.DataFrame,
        regime_panel: pd.DataFrame,
        returns_panel: pd.DataFrame,
        instrument_ids: list[str],
    ) -> None:
        """Train a quarterly LightGBM regressor."""
        import lightgbm as lgb

        feature_df, signal_cols, instr_cols = _build_gbm_features(
            signal_panel=signal_panel,
            regime_panel=regime_panel,
            instrument_ids=instrument_ids,
        )
        if feature_df.empty:
            self._state = None
            return

        ret_by_key: dict[tuple[datetime, str], float] = {
            (row["value_ts"], row["instrument_id"]): float(row["next_log_ret"])
            for _, row in returns_panel.iterrows()
        }
        y: list[float] = []
        keep_rows: list[int] = []
        for i, (ts, inst) in enumerate(zip(
            feature_df["value_ts"], feature_df["instrument_id"], strict=False
        )):
            r = ret_by_key.get((ts, inst))
            if r is None or not np.isfinite(r):
                continue
            y.append(r)
            keep_rows.append(i)
        if len(y) < 200:
            self._state = None
            return

        Xdf = feature_df.iloc[keep_rows]
        feature_columns = [
            c for c in Xdf.columns if c not in ("value_ts", "instrument_id")
        ]
        X = Xdf[feature_columns].astype(float).values
        y_arr = np.array(y, dtype=float)

        # Time-ordered validation split.
        n = len(y_arr)
        split = int(n * (1.0 - self.validation_fold_fraction))
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y_arr[:split], y_arr[split:]

        model = lgb.LGBMRegressor(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            min_child_samples=self.min_child_samples,
            random_state=self.random_state,
            verbose=-1,
        )
        fit_kwargs: dict[str, Any] = {}
        if len(X_val) > 0:
            fit_kwargs["eval_set"] = [(X_val, y_val)]
            fit_kwargs["callbacks"] = [
                lgb.early_stopping(self.early_stopping_rounds),
            ]
        model.fit(X_train, y_train, **fit_kwargs)

        self._state = {
            "model": model,
            "feature_columns": feature_columns,
            "signal_columns": signal_cols,
            "instrument_columns": instr_cols,
            "fit_as_of": datetime.utcnow().isoformat(),
            "n_observations": int(n),
        }

    def compute(
        self, data: CompositeInput, session: Session | None
    ) -> list[CompositeOutput]:
        if session is None:
            raise ValueError("GBMComposite requires a DB session")
        if self._state is None:
            log.info("composite.gbm.no_state")
            return []

        signal_panel = _load_signal_panel(
            session,
            signal_method_ids=data.designated_signal_method_ids,
            instrument_ids=data.instrument_ids,
            start=data.start,
            end=data.end,
            as_of=data.as_of,
        )
        if signal_panel.empty:
            return []
        regime_panel = _load_regime_panel(
            session,
            regime_method_id=data.regime_method_id,
            start=data.start,
            end=data.end,
            as_of=data.as_of,
        )
        cp_prob = latest_changepoint_probability(
            session,
            bocpd_method_id=data.bocpd_method_id,
            as_of=data.as_of,
        )

        feature_df, _, _ = _build_gbm_features(
            signal_panel=signal_panel,
            regime_panel=regime_panel,
            instrument_ids=data.instrument_ids,
            changepoint_probability=cp_prob,
        )
        if feature_df.empty:
            return []

        feature_columns: list[str] = self._state["feature_columns"]
        # Backfill missing columns with 0 (regime cols, instrument
        # one-hots that didn't appear in compute window).
        for c in feature_columns:
            if c not in feature_df.columns:
                feature_df[c] = 0.0
        Xc = feature_df[feature_columns].astype(float).values

        model = self._state["model"]
        preds = model.predict(Xc)

        # Per (value_ts, instrument) row: build CompositeOutput.
        regime_by_ts: dict[datetime, dict[str, object]] = {
            row["value_ts"]: row for _, row in regime_panel.iterrows()
        }
        outputs: list[CompositeOutput] = []
        for i, (_, row) in enumerate(feature_df.iterrows()):
            value_ts = row["value_ts"]
            instrument_id = row["instrument_id"]
            raw_score = float(preds[i])
            clipped = max(-self.clip_z, min(self.clip_z, raw_score))
            score = float(np.tanh(clipped))

            row_signals = signal_panel[
                (signal_panel["value_ts"] == value_ts)
                & (signal_panel["instrument_id"] == instrument_id)
            ]
            if len(row_signals) < self.min_signals_for_score:
                continue

            regime_info = regime_by_ts.get(value_ts)
            label = str(regime_info["label"]) if regime_info is not None else None
            confidence = float(row_signals["confidence"].median())

            metadata = {
                "method": self.metadata.method_id,
                "transition_probability": (
                    float(cp_prob) if cp_prob is not None else None
                ),
                "n_features": len(feature_columns),
                "regime_probability_vector": (
                    dict(cast(dict[str, float], regime_info["probability_vector"]))
                    if regime_info is not None
                    else None
                ),
            }
            outputs.append(
                CompositeOutput(
                    instrument_id=str(instrument_id),
                    value_ts=value_ts,
                    observation_ts=data.as_of,
                    raw_score=raw_score,
                    score=score,
                    confidence=confidence,
                    regime_label=label,
                    n_signals_used=len(row_signals),
                    metadata=metadata,
                )
            )
        return outputs

    def serialize(self) -> bytes:
        if self._state is None:
            return b""
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def deserialize(cls, blob: bytes) -> Self:
        if not _lightgbm_available():
            raise RuntimeError(
                "GBMComposite cannot deserialize without lightgbm"
            )
        m = cls()
        if blob:
            m._state = pickle.loads(blob)
        return m


def _build_gbm_features(
    *,
    signal_panel: pd.DataFrame,
    regime_panel: pd.DataFrame,
    instrument_ids: list[str],
    changepoint_probability: float | None = None,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Build the wide feature panel for the GBM model.

    Columns:
    - One column per signal_method_id (z * confidence).
    - 5 ``regime_prob_<r>`` columns.
    - ``changepoint_probability`` (optional, scalar broadcast).
    - One ``instr_<id>`` column per instrument in ``instrument_ids``.
    """
    if signal_panel.empty:
        return pd.DataFrame(), [], []
    sp = signal_panel.copy()
    sp["x"] = sp["z"] * sp["confidence"]
    wide_signals = (
        sp.pivot_table(
            index=["value_ts", "instrument_id"],
            columns="signal_method_id",
            values="x",
            aggfunc="last",
        )
        .fillna(0.0)
        .reset_index()
    )
    signal_cols = [
        c for c in wide_signals.columns if c not in ("value_ts", "instrument_id")
    ]

    regime_cols: list[str] = []
    if not regime_panel.empty:
        regime_records: list[dict[str, object]] = []
        for _, row in regime_panel.iterrows():
            rec: dict[str, object] = {"value_ts": row["value_ts"]}
            for r in NAMED_REGIMES:
                rec[f"regime_prob_{r}"] = float(
                    (row["probability_vector"] or {}).get(r, 0.0)
                )
            regime_records.append(rec)
        rdf = pd.DataFrame(regime_records)
        regime_cols = [c for c in rdf.columns if c.startswith("regime_prob_")]
        wide_signals = wide_signals.merge(rdf, on="value_ts", how="left")
        wide_signals[regime_cols] = wide_signals[regime_cols].fillna(
            1.0 / max(len(NAMED_REGIMES), 1)
        )
    else:
        for r in NAMED_REGIMES:
            wide_signals[f"regime_prob_{r}"] = 1.0 / max(len(NAMED_REGIMES), 1)
            regime_cols.append(f"regime_prob_{r}")

    wide_signals["changepoint_probability"] = (
        float(changepoint_probability) if changepoint_probability is not None else 0.0
    )

    instr_cols: list[str] = []
    for inst in instrument_ids:
        col = f"instr_{inst}"
        wide_signals[col] = (wide_signals["instrument_id"] == inst).astype(float)
        instr_cols.append(col)

    return wide_signals, signal_cols, instr_cols


__all__ = [
    "BayesianHierarchicalComposite",
    "CompositeInput",
    "CompositeMethod",
    "CompositeOutput",
    "GBMComposite",
    "LinearComposite",
    "_lightgbm_available",
]
