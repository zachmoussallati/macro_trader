"""Data-quality method implementations.

Two methods in Stage 2:

- ``ZScoreOutlier``  — BASELINE: rolling |z| > threshold per series.
- ``IsolationForestOutlier`` — SHADOW: sklearn IsolationForest per series.

Both consume a 1-D numeric ``np.ndarray`` and return a bool mask of the
same length (``True`` = flagged as outlier).
"""

from __future__ import annotations

import pickle

import numpy as np

from macro_trader.methods.base import Method, MethodMetadata


class ZScoreOutlier(Method[np.ndarray, np.ndarray]):
    """Flag |z| > threshold over a rolling window per series.

    Defaults: ``threshold=3.0``, ``window=60``. With <window observations
    the first ``window-1`` outputs are False (we can't compute a z-score on
    too little data).
    """

    metadata = MethodMetadata(
        method_id="data_quality.zscore.v1",
        component="data_quality",
        name="Rolling z-score outlier flag",
        version="1.0.0",
        description="Flag |z| > 3 over a 60 trading-day rolling window per series.",
        references=[],
    )

    def __init__(self, *, threshold: float = 3.0, window: int = 60) -> None:
        self.threshold = float(threshold)
        self.window = int(window)

    # ------------------------------------------------------------------
    # Method interface
    # ------------------------------------------------------------------
    def fit(self, data: np.ndarray) -> None:  # no-op
        return None

    def predict(self, data: np.ndarray) -> np.ndarray:
        arr = np.asarray(data, dtype=float)
        n = arr.size
        out = np.zeros(n, dtype=bool)
        if n < self.window:
            return out

        # Rolling mean + std using cumulative-sum trick. NaNs handled by
        # falling back to per-window numpy computations.
        for i in range(self.window - 1, n):
            window = arr[i - self.window + 1 : i + 1]
            window = window[~np.isnan(window)]
            if window.size < 2:
                continue
            mu = float(window.mean())
            sigma = float(window.std(ddof=1))
            x = arr[i]
            if np.isnan(x) or sigma == 0.0:
                continue
            z = abs((x - mu) / sigma)
            if z > self.threshold:
                out[i] = True
        return out

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def serialize(self) -> bytes:
        return pickle.dumps({"threshold": self.threshold, "window": self.window})

    @classmethod
    def deserialize(cls, blob: bytes) -> ZScoreOutlier:
        state = pickle.loads(blob)
        return cls(threshold=state["threshold"], window=state["window"])


class IsolationForestOutlier(Method[np.ndarray, np.ndarray]):
    """sklearn IsolationForest per series, contamination = 0.01 by default.

    ``fit`` trains a fresh estimator. ``predict`` flags points labelled
    ``-1`` by the estimator. ``serialize`` pickles the fitted model;
    ``deserialize`` restores it.
    """

    metadata = MethodMetadata(
        method_id="data_quality.isoforest.v1",
        component="data_quality",
        name="Isolation Forest outlier",
        version="1.0.0",
        description="sklearn IsolationForest, contamination=0.01, per-series.",
        references=["Liu, Ting, Zhou 2008"],
    )

    def __init__(
        self,
        *,
        contamination: float = 0.01,
        random_state: int = 42,
        n_estimators: int = 100,
    ) -> None:
        self.contamination = float(contamination)
        self.random_state = int(random_state)
        self.n_estimators = int(n_estimators)
        self._estimator: object | None = None

    def fit(self, data: np.ndarray) -> None:
        from sklearn.ensemble import IsolationForest

        arr = np.asarray(data, dtype=float)
        arr = arr[~np.isnan(arr)]
        if arr.size < 10:
            self._estimator = None
            return
        estimator = IsolationForest(
            contamination=self.contamination,
            random_state=self.random_state,
            n_estimators=self.n_estimators,
        )
        estimator.fit(arr.reshape(-1, 1))
        self._estimator = estimator

    def predict(self, data: np.ndarray) -> np.ndarray:
        arr = np.asarray(data, dtype=float)
        out = np.zeros(arr.size, dtype=bool)
        if self._estimator is None:
            # Convenience: fit-then-predict if caller didn't fit. Tests use this.
            self.fit(arr)
            if self._estimator is None:
                return out
        valid_mask = ~np.isnan(arr)
        if not valid_mask.any():
            return out
        valid_values = arr[valid_mask].reshape(-1, 1)
        labels = self._estimator.predict(valid_values)  # type: ignore[attr-defined]
        out[valid_mask] = labels == -1
        return out

    def serialize(self) -> bytes:
        return pickle.dumps(
            {
                "contamination": self.contamination,
                "random_state": self.random_state,
                "n_estimators": self.n_estimators,
                "estimator": self._estimator,
            }
        )

    @classmethod
    def deserialize(cls, blob: bytes) -> IsolationForestOutlier:
        state = pickle.loads(blob)
        inst = cls(
            contamination=state["contamination"],
            random_state=state["random_state"],
            n_estimators=state["n_estimators"],
        )
        inst._estimator = state.get("estimator")
        return inst
