"""Five regime classification methods.

All produce ``RegimeOutput`` rows with a discrete label + full
probability vector. The Stage 6 prompt defines the 5 named regimes
in ``regime/__init__.py:NAMED_REGIMES``.

The Method[InputT, OutputT] interface from the framework is
abused mildly: regime classification doesn't fit the SignalMethod
shape (no per-instrument output, no raw_value), so methods here
subclass ``Method`` directly with a dedicated ``RegimeOutput``
dataclass.

Refit cadence:
- ``regime.rules.v1``: stateless, no refit.
- ``regime.gmm.v1``: weekly Sunday 04:00 UTC.
- ``regime.hmm.v1``: quarterly (gated on hmmlearn).
- ``regime.msvar.v1``: quarterly (univariate fallback via
  statsmodels MarkovRegression).
- ``regime.bocpd.v1``: stateless (online algorithm); label
  inherited from rules baseline.
"""

# Stat-ML convention uses uppercase X / W / V — suppress N803 / N806
# file-wide so np conventions stay readable.
# ruff: noqa: N803, N806

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture

from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import Method, MethodMetadata
from macro_trader.regime import NAMED_REGIMES
from macro_trader.regime.features import REGIME_FEATURE_COLUMNS, build_regime_features
from macro_trader.regime.labeling import (
    anchor_centroids_matrix,
    map_centroids_to_labels,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def _hmmlearn_available() -> bool:
    try:
        import hmmlearn.hmm  # noqa: F401
    except Exception:
        return False
    return True


@dataclass(slots=True)
class RegimeInput:
    as_of: datetime
    lookback_days: int = 504


@dataclass(slots=True)
class RegimeOutput:
    value_ts: datetime
    observation_ts: datetime
    label: str
    probability_vector: dict[str, float]
    confidence: float
    transition_prob: float | None = None
    days_in_regime: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Method 1: Rules baseline
# ----------------------------------------------------------------------
class RulesRegimeClassifier(Method[RegimeInput, list[RegimeOutput]]):
    """Decision-tree rules over VIX + growth/inflation z + curve.

    Pure logic; no fitting. Probability vector is one-hot on the
    assigned label. Confidence always 1.0. Acts as BASELINE.
    """

    metadata = MethodMetadata(
        method_id="regime.rules.v1",
        component="regime_classifier",
        name="Rules-Based Regime Classifier",
        version="1.0.0",
        description=(
            "Transparent decision-tree labels from VIX + yield curve + "
            "USD + growth/inflation z."
        ),
        references=[],
    )

    def __init__(
        self,
        *,
        vix_high: float = 30.0,
        vix_low: float = 15.0,
        realized_vol_high: float = 0.25,
        growth_z_threshold: float = 0.5,
        inflation_z_threshold: float = 0.5,
        usd_z_neutral_band: float = 0.5,
    ) -> None:
        self.vix_high = float(vix_high)
        self.vix_low = float(vix_low)
        self.realized_vol_high = float(realized_vol_high)
        self.growth_z_threshold = float(growth_z_threshold)
        self.inflation_z_threshold = float(inflation_z_threshold)
        self.usd_z_neutral_band = float(usd_z_neutral_band)
        self._state: dict[str, Any] | None = None

    def fit(self, data: RegimeInput) -> None:
        return None

    def predict(self, data: RegimeInput) -> list[RegimeOutput]:
        raise RuntimeError("use compute(data, session)")

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> Self:
        return cls()

    def classify_row(self, row: pd.Series) -> tuple[str, dict[str, float]]:
        """Apply the decision tree to one feature row."""
        vix = float(row.get("vix_level", np.nan) or np.nan)
        rv = float(row.get("realized_vol_60d", np.nan) or np.nan)
        gz = float(row.get("growth", 0.0) or 0.0)
        iz = float(row.get("inflation", 0.0) or 0.0)
        uz = float(row.get("usd", 0.0) or 0.0)
        slope = float(row.get("yield_curve_slope", np.nan) or 0.0)

        label = "risk_on_growth"
        if (
            np.isfinite(vix)
            and np.isfinite(rv)
            and vix > self.vix_high
            and rv > self.realized_vol_high
        ):
            label = "vol_spike"
        elif gz > self.growth_z_threshold and iz > self.inflation_z_threshold:
            label = "stagflation"
        elif gz < -self.growth_z_threshold and (np.isfinite(vix) and vix > 20.0):
            label = "risk_off_defensive"
        elif (
            np.isfinite(vix)
            and vix < self.vix_low
            and slope > 0.0
            and abs(uz) < self.usd_z_neutral_band
        ):
            label = "carry_friendly"

        vec = dict.fromkeys(NAMED_REGIMES, 0.0)
        vec[label] = 1.0
        return label, vec

    def compute(
        self, data: RegimeInput, session: Session | None
    ) -> list[RegimeOutput]:
        if session is None:
            raise ValueError("RulesRegimeClassifier requires a DB session")
        panel = build_regime_features(
            session, as_of=data.as_of, lookback_days=data.lookback_days
        )
        if panel.empty:
            return []
        outputs: list[RegimeOutput] = []
        last_label = ""
        run_length = 0
        for ts, row in panel.iterrows():
            label, vec = self.classify_row(row)
            run_length = 1 if label != last_label else run_length + 1
            outputs.append(
                RegimeOutput(
                    value_ts=ts.to_pydatetime(),
                    observation_ts=data.as_of,
                    label=label,
                    probability_vector=vec,
                    confidence=1.0,
                    transition_prob=None,
                    days_in_regime=run_length,
                    metadata={"method": "rules"},
                )
            )
            last_label = label
        return outputs


# ----------------------------------------------------------------------
# Shared base for fitted-state methods (GMM / HMM / MS-VAR)
# ----------------------------------------------------------------------
class _FittedRegimeBase(Method[RegimeInput, list[RegimeOutput]]):
    """Holds the centroid-anchored-labelling lifecycle shared by
    GMM / HMM / MS-VAR. Subclasses implement ``_fit_model(X)`` and
    ``_predict_probs(model, X)``."""

    def __init__(self, *, lookback_days: int = 504) -> None:
        self.lookback_days = int(lookback_days)
        self._state: dict[str, Any] | None = None

    def fit(self, data: RegimeInput) -> None:
        raise RuntimeError("use fit_on_panel(panel)")

    def predict(self, data: RegimeInput) -> list[RegimeOutput]:
        raise RuntimeError("use compute(data, session)")

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

    def _fit_model(self, X: np.ndarray) -> tuple[object, np.ndarray]:
        raise NotImplementedError

    def _predict_probs(self, model: object, X: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def fit_on_panel(self, panel: pd.DataFrame) -> None:
        if panel.empty or panel.shape[1] == 0:
            self._state = None
            return
        X = panel.dropna(how="any").values
        if X.shape[0] < 20 or X.shape[1] < 3:
            self._state = None
            return

        model, centroids = self._fit_model(X)

        feature_cols = list(panel.columns)
        if self._state is not None and "centroids" in self._state:
            prior_centroids = np.asarray(self._state["centroids"])
            prior_labels = list(self._state["labels"])
        else:
            prior_centroids = anchor_centroids_matrix(
                feature_cols, list(NAMED_REGIMES)
            )
            prior_labels = list(NAMED_REGIMES)
        labels, mapping_summary = map_centroids_to_labels(
            centroids, prior_centroids, prior_labels
        )

        self._state = {
            "model": model,
            "centroids": centroids,
            "labels": labels,
            "feature_columns": feature_cols,
            "mapping_summary": mapping_summary,
            "fit_as_of": datetime.utcnow().isoformat(),
        }

    def compute(
        self, data: RegimeInput, session: Session | None
    ) -> list[RegimeOutput]:
        if session is None:
            raise ValueError(f"{type(self).__name__} requires a DB session")
        panel = build_regime_features(
            session, as_of=data.as_of, lookback_days=data.lookback_days
        )
        if panel.empty:
            return []
        if self._state is None:
            log.info("regime.fallback_fit", method=self.metadata.method_id)
            self.fit_on_panel(panel)
            if self._state is None:
                return []

        feature_cols: list[str] = self._state["feature_columns"]
        X_panel = panel[[c for c in feature_cols if c in panel.columns]].dropna(
            how="any"
        )
        if X_panel.empty:
            return []
        # Pad columns absent from the latest panel with zeros so the
        # fitted model still gets the right feature count.
        X = np.zeros((X_panel.shape[0], len(feature_cols)))
        for j, col in enumerate(feature_cols):
            if col in X_panel.columns:
                X[:, j] = X_panel[col].values

        probs = self._predict_probs(self._state["model"], X)
        labels = list(self._state["labels"])
        outputs: list[RegimeOutput] = []
        last_label = ""
        run_length = 0
        for i, ts in enumerate(X_panel.index):
            merged: dict[str, float] = dict.fromkeys(NAMED_REGIMES, 0.0)
            for k in range(len(labels)):
                merged[labels[k]] = merged.get(labels[k], 0.0) + float(probs[i, k])
            label = max(merged.items(), key=lambda kv: kv[1])[0]
            run_length = 1 if label != last_label else run_length + 1
            outputs.append(
                RegimeOutput(
                    value_ts=ts.to_pydatetime(),
                    observation_ts=data.as_of,
                    label=label,
                    probability_vector=merged,
                    confidence=float(max(merged.values())),
                    transition_prob=None,
                    days_in_regime=run_length,
                    metadata={"method": self.metadata.method_id},
                )
            )
            last_label = label
        return outputs


# ----------------------------------------------------------------------
# Method 2: GMM
# ----------------------------------------------------------------------
class GMMRegimeClassifier(_FittedRegimeBase):
    metadata = MethodMetadata(
        method_id="regime.gmm.v1",
        component="regime_classifier",
        name="Gaussian Mixture Regime Classifier",
        version="1.0.0",
        description="K=5 GaussianMixture with centroid-anchored labels.",
        references=["McLachlan & Peel (2000) Finite Mixture Models"],
    )

    def __init__(
        self,
        *,
        lookback_days: int = 504,
        n_components: int = 5,
        covariance_type: str = "full",
        random_state: int = 42,
    ) -> None:
        super().__init__(lookback_days=lookback_days)
        self.n_components = int(n_components)
        self.covariance_type = covariance_type
        self.random_state = int(random_state)

    def _fit_model(self, X: np.ndarray) -> tuple[object, np.ndarray]:
        model = GaussianMixture(
            n_components=self.n_components,
            covariance_type=self.covariance_type,
            random_state=self.random_state,
            n_init=2,
        )
        model.fit(X)
        return model, model.means_

    def _predict_probs(self, model: object, X: np.ndarray) -> np.ndarray:
        probs: np.ndarray = model.predict_proba(X)  # type: ignore[attr-defined]
        return probs


# ----------------------------------------------------------------------
# Method 3: HMM (gated on hmmlearn)
# ----------------------------------------------------------------------
class HMMRegimeClassifier(_FittedRegimeBase):
    metadata = MethodMetadata(
        method_id="regime.hmm.v1",
        component="regime_classifier",
        name="Hidden Markov Regime Classifier",
        version="1.0.0",
        description="K=5 Gaussian HMM; centroid-anchored labels; quarterly refit.",
        references=["Rabiner (1989) HMM Tutorial"],
    )

    def __init__(
        self,
        *,
        lookback_days: int = 1260,
        n_components: int = 5,
        covariance_type: str = "full",
        n_iter: int = 200,
        random_state: int = 42,
    ) -> None:
        if not _hmmlearn_available():
            raise RuntimeError(
                "HMMRegimeClassifier requires hmmlearn (in core deps); "
                "install via `uv sync`."
            )
        super().__init__(lookback_days=lookback_days)
        self.n_components = int(n_components)
        self.covariance_type = covariance_type
        self.n_iter = int(n_iter)
        self.random_state = int(random_state)

    def _fit_model(self, X: np.ndarray) -> tuple[object, np.ndarray]:
        from hmmlearn.hmm import GaussianHMM

        model = GaussianHMM(
            n_components=self.n_components,
            covariance_type=self.covariance_type,
            n_iter=self.n_iter,
            random_state=self.random_state,
        )
        model.fit(X)
        return model, model.means_

    def _predict_probs(self, model: object, X: np.ndarray) -> np.ndarray:
        # Stage 6 decisions.md note: hmmlearn's predict_proba uses
        # smoothing (forward-backward). For real-time use, only the
        # *latest row* matters in production; for the attribution
        # history this is the correct retrospective output. The
        # daily runner persists only the latest row per observation_ts,
        # which collapses smoothing's future-info issue.
        probs: np.ndarray = model.predict_proba(X)  # type: ignore[attr-defined]
        return probs


# ----------------------------------------------------------------------
# Method 4: MS-VAR (univariate fallback via PCA + statsmodels)
# ----------------------------------------------------------------------
class MSVARRegimeClassifier(_FittedRegimeBase):
    metadata = MethodMetadata(
        method_id="regime.msvar.v1",
        component="regime_classifier",
        name="Markov-Switching Regression Regime Classifier",
        version="1.0.0",
        description=(
            "K=5 Markov-switching mean on PC1 of the feature panel "
            "(univariate fallback for full MS-VAR per Stage 6 prompt's "
            "allowance)."
        ),
        references=[
            "Hamilton (1989) A New Approach to the Economic Analysis of Nonstationary Time Series",
            "Krolzig (1997) Markov-Switching Vector Autoregressions",
        ],
    )

    def __init__(
        self,
        *,
        lookback_days: int = 1260,
        n_regimes: int = 5,
        n_iter: int = 500,
    ) -> None:
        super().__init__(lookback_days=lookback_days)
        self.n_regimes = int(n_regimes)
        self.n_iter = int(n_iter)

    def _fit_model(self, X: np.ndarray) -> tuple[object, np.ndarray]:
        from sklearn.decomposition import PCA
        from statsmodels.tsa.regime_switching.markov_regression import (
            MarkovRegression,
        )

        pca = PCA(n_components=1)
        y = pca.fit_transform(X).reshape(-1)

        model = MarkovRegression(
            y,
            k_regimes=self.n_regimes,
            trend="c",
            switching_variance=True,
        )
        result = model.fit(maxiter=self.n_iter, disp=False)

        smoothed = np.asarray(result.smoothed_marginal_probabilities)
        assignments = smoothed.argmax(axis=1)
        centroids = np.zeros((self.n_regimes, X.shape[1]))
        for k in range(self.n_regimes):
            mask = assignments == k
            if mask.sum() > 0:
                centroids[k] = X[mask].mean(axis=0)
        bundle = {"pca": pca, "result": result, "fit_n": len(y)}
        return bundle, centroids

    def _predict_probs(self, model: object, X: np.ndarray) -> np.ndarray:
        bundle: dict[str, Any] = model  # type: ignore[assignment]
        pca = bundle["pca"]
        result = bundle["result"]
        y = pca.transform(X).reshape(-1)
        smoothed = np.asarray(result.smoothed_marginal_probabilities)
        if smoothed.shape[0] == len(y):
            return smoothed
        n = min(smoothed.shape[0], len(y))
        out = np.zeros((len(y), self.n_regimes))
        out[:n] = smoothed[:n]
        if len(y) > n:
            out[n:] = smoothed[-1]
        return out


# ----------------------------------------------------------------------
# Method 5: BOCPD (label inherited from rules; changepoint prob is novel)
# ----------------------------------------------------------------------
class BOCPDRegimeClassifier(Method[RegimeInput, list[RegimeOutput]]):
    """Bayesian Online Changepoint Detection (Adams & MacKay 2007).

    Novel output: per-day changepoint probability. Label inherited
    from the rules baseline so the row still has a usable label
    column; the value-add is the changepoint signal.
    """

    metadata = MethodMetadata(
        method_id="regime.bocpd.v1",
        component="regime_classifier",
        name="Bayesian Online Changepoint Detection",
        version="1.0.0",
        description=(
            "Real-time changepoint probability via Adams & MacKay 2007; "
            "label inherited from regime.rules.v1."
        ),
        references=["Adams & MacKay (2007) Bayesian Online Changepoint Detection"],
    )

    def __init__(
        self, *, hazard_lambda: int = 60, lookback_days: int = 504
    ) -> None:
        self.hazard_lambda = int(hazard_lambda)
        self.lookback_days = int(lookback_days)
        self._state: dict[str, Any] | None = None

    def fit(self, data: RegimeInput) -> None:
        return None

    def predict(self, data: RegimeInput) -> list[RegimeOutput]:
        raise RuntimeError("use compute(data, session)")

    def serialize(self) -> bytes:
        return b""

    @classmethod
    def deserialize(cls, blob: bytes) -> Self:
        return cls()

    def _run_bocpd(self, x: np.ndarray) -> np.ndarray:
        """Constant-hazard Gaussian-emission BOCPD over a scalar
        series ``x`` (Adams & MacKay 2007).

        Tracks joint run-length probabilities and reports the
        probability that the current run length is short
        ``P(r_t < short_horizon | x_{1:t})`` -- the canonical
        "changepoint just happened" indicator. Note: the strict
        ``P(r_t = 0 | x_{1:t})`` is mathematically equal to the
        constant hazard ``H`` and therefore not informative; we
        use the cumulative short-horizon probability instead.

        Numerical stability: re-normalise the joint every 50 steps
        to avoid underflow over long series.
        """
        T = len(x)
        if T == 0:
            return np.array([])
        H = 1.0 / self.hazard_lambda

        mu_0, kappa_0, alpha_0, beta_0 = 0.0, 1.0, 2.0, 1.0

        mu = np.array([mu_0])
        kappa = np.array([kappa_0])
        alpha = np.array([alpha_0])
        beta = np.array([beta_0])
        joint = np.array([1.0])  # P(r_t, x_{1:t}) - joint, not normalised

        cp_probs = np.zeros(T)
        for t in range(T):
            xt = float(x[t])
            # Predictive p(x_t | run length r) under each current
            # run-length cluster.
            sigma2 = beta * (kappa + 1) / (alpha * kappa)
            pred = (
                1.0
                / np.sqrt(2 * np.pi * sigma2)
                * np.exp(-0.5 * (xt - mu) ** 2 / sigma2)
            )

            # Growth: run length increments by 1 (no changepoint).
            growth = joint * pred * (1.0 - H)
            # Changepoint: new run length = 0; marginalise over prior r.
            cp = float((joint * pred * H).sum())
            joint = np.concatenate([[cp], growth])

            total = float(joint.sum())
            if total > 0:
                # Skip the warmup window (joint hasn't had enough steps
                # to populate the short-horizon mass meaningfully).
                short_horizon = 5
                if len(joint) <= short_horizon:
                    cp_probs[t] = 0.0
                else:
                    cp_probs[t] = float(
                        joint[:short_horizon].sum() / total
                    )
                if t % 50 == 49:
                    joint = joint / total  # rescale to avoid underflow
            else:
                cp_probs[t] = 0.0
                joint = np.concatenate([[1.0], np.zeros(t + 1)])

            new_mu = (kappa * mu + xt) / (kappa + 1)
            new_kappa = kappa + 1
            new_alpha = alpha + 0.5
            new_beta = beta + (kappa * (xt - mu) ** 2) / (2 * (kappa + 1))
            mu = np.concatenate([[mu_0], new_mu])
            kappa = np.concatenate([[kappa_0], new_kappa])
            alpha = np.concatenate([[alpha_0], new_alpha])
            beta = np.concatenate([[beta_0], new_beta])
        return cp_probs

    def compute(
        self, data: RegimeInput, session: Session | None
    ) -> list[RegimeOutput]:
        if session is None:
            raise ValueError("BOCPDRegimeClassifier requires a DB session")
        panel = build_regime_features(
            session, as_of=data.as_of, lookback_days=data.lookback_days
        )
        if panel.empty:
            return []
        from sklearn.decomposition import PCA

        clean = panel.dropna(how="any")
        if clean.empty or clean.shape[1] < 2:
            return []
        pca = PCA(n_components=1)
        x = pca.fit_transform(clean.values).reshape(-1)
        cp_probs = self._run_bocpd(x)

        rules = RulesRegimeClassifier()
        labels: list[str] = []
        for _, row in clean.iterrows():
            lab, _vec = rules.classify_row(row)
            labels.append(lab)

        outputs: list[RegimeOutput] = []
        for i, ts in enumerate(clean.index):
            vec = dict.fromkeys(NAMED_REGIMES, 0.0)
            vec[labels[i]] = 1.0
            outputs.append(
                RegimeOutput(
                    value_ts=ts.to_pydatetime(),
                    observation_ts=data.as_of,
                    label=labels[i],
                    probability_vector=vec,
                    confidence=1.0,
                    transition_prob=float(cp_probs[i]),
                    days_in_regime=None,
                    metadata={
                        "method": "bocpd",
                        "changepoint_probability": float(cp_probs[i]),
                        "label_source": "rules.v1",
                    },
                )
            )
        return outputs


__all__ = [
    "BOCPDRegimeClassifier",
    "GMMRegimeClassifier",
    "HMMRegimeClassifier",
    "MSVARRegimeClassifier",
    "RegimeInput",
    "RegimeOutput",
    "RulesRegimeClassifier",
    "_hmmlearn_available",
]
_ = REGIME_FEATURE_COLUMNS  # silence unused
