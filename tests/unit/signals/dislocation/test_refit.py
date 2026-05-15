"""Unit tests for the dislocation weekly-refit logic.

Covers:

- ``PCADislocation`` serialize / deserialize round-trip yields the
  same predictions on identical input.
- PCA sign alignment correctly flips a component whose sign was
  inverted relative to the prior week's fit.
- Compression helpers round-trip.
"""

from __future__ import annotations

import pickle
import zlib

import numpy as np
import pandas as pd
import pytest

from macro_trader.signals.dislocation.methods import PCADislocation
from macro_trader.signals.dislocation.refit import (
    _maybe_compress,
    _maybe_decompress,
)


def _synthetic_returns(n_days: int = 400, n_instruments: int = 5, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    loadings = rng.normal(scale=0.5, size=(n_instruments, 2))
    factors = rng.normal(scale=0.01, size=(n_days, 2))
    returns = factors @ loadings.T + rng.normal(
        scale=0.005, size=(n_days, n_instruments)
    )
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B", tz="UTC")
    cols = [f"I{i}" for i in range(n_instruments)]
    return pd.DataFrame(returns, index=dates, columns=cols)


@pytest.mark.unit
def test_pca_serialize_round_trip_preserves_predictions() -> None:
    """Pickle round-trip must produce identical signal output for the
    same input."""
    returns = _synthetic_returns(n_days=300, n_instruments=5)
    method = PCADislocation(n_components=2)
    method.fit_on_returns(returns)
    blob = method.serialize()
    assert blob, "expected non-empty serialized blob"

    restored = PCADislocation.deserialize(blob)
    pred_a = method.predict_on_returns(returns)
    pred_b = restored.predict_on_returns(returns)
    pd.testing.assert_frame_equal(pred_a, pred_b)


@pytest.mark.unit
def test_pca_serialize_empty_when_unfit() -> None:
    """An uninitialised method serialises to an empty blob; deserialising
    that returns an unfitted instance."""
    method = PCADislocation()
    assert method.serialize() == b""

    restored = PCADislocation.deserialize(b"")
    assert restored._state is None


@pytest.mark.unit
def test_pca_align_signs_to_prior_flips_inverted_component() -> None:
    """If a freshly-fit PCA produces a component that's the negation of
    last week's matching component, ``align_signs_to`` flips it back."""
    returns = _synthetic_returns(n_days=300, n_instruments=5, seed=0)

    prior = PCADislocation(n_components=2)
    prior.fit_on_returns(returns)
    assert prior._state is not None

    new = PCADislocation(n_components=2)
    new.fit_on_returns(returns)
    assert new._state is not None

    # Flip component 0's sign in the new fit by hand.
    new._state["pca"].components_[0, :] *= -1

    # Sanity: post-flip, component 0 anti-correlates with the prior's.
    cor_pre = np.corrcoef(
        new._state["pca"].components_[0],
        prior._state["pca"].components_[0],
    )[0, 1]
    assert cor_pre < -0.8

    new.align_signs_to(prior._state)

    cor_post = np.corrcoef(
        new._state["pca"].components_[0],
        prior._state["pca"].components_[0],
    )[0, 1]
    assert cor_post > 0.8


@pytest.mark.unit
def test_pca_align_signs_noop_when_no_prior_state() -> None:
    """``align_signs_to`` with an empty prior is a no-op."""
    method = PCADislocation(n_components=2)
    method.fit_on_returns(_synthetic_returns(n_days=300, n_instruments=5))
    components_before = method._state["pca"].components_.copy()
    method.align_signs_to({})
    np.testing.assert_array_equal(
        components_before, method._state["pca"].components_
    )


@pytest.mark.unit
def test_compression_threshold_passthrough_for_small_blobs() -> None:
    """Below the threshold, no compression header is added."""
    blob = b"small payload"
    final, compressed = _maybe_compress(blob)
    assert compressed is False
    assert final == blob
    assert _maybe_decompress(final) == blob


@pytest.mark.unit
def test_compression_round_trip_for_large_blobs() -> None:
    """Above the threshold the blob is zlib-prefixed; decompression
    recovers the original bytes."""
    payload = pickle.dumps(np.random.rand(20_000))
    final, compressed = _maybe_compress(payload)
    assert compressed is True
    assert final.startswith(b"ZLIB")
    assert _maybe_decompress(final) == payload
    # Sanity: zlib actually compressed (final smaller than raw payload+4).
    assert len(final) < len(payload)
    # Sanity: the compressed bytes themselves look like a valid zlib stream.
    zlib.decompress(final[4:])


@pytest.mark.unit
def test_pca_predict_returns_empty_when_unfit() -> None:
    method = PCADislocation()
    out = method.predict_on_returns(_synthetic_returns(n_days=10, n_instruments=4))
    assert out.empty


@pytest.mark.unit
def test_pca_compute_falls_back_to_fit_when_no_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """``compute()`` should fit on the daily window (and log a warning)
    if there's no cached state — graceful degradation on first runs or
    after a missed weekly refit."""
    from datetime import datetime, timedelta
    from typing import Any

    from macro_trader.signals.base import SignalInput

    panel = _synthetic_returns(n_days=300, n_instruments=5)
    # Convert returns back to "prices" for the loader stub.
    prices = (panel + 1).cumprod() * 100.0

    def _fake_loader(
        session: Any,
        instrument_ids: list[str],
        *,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
        calendar: str = "NYSE",
        ffill_limit: int = 1,
    ) -> pd.DataFrame:
        return prices.loc[:, [c for c in instrument_ids if c in prices.columns]]

    monkeypatch.setattr(
        "macro_trader.signals.dislocation.methods.load_close_panel", _fake_loader
    )

    end = prices.index[-1].to_pydatetime()
    inp = SignalInput(
        instrument_ids=list(prices.columns),
        as_of=end,
        start=(prices.index[-1] - timedelta(days=10)).to_pydatetime(),
        end=end,
    )
    method = PCADislocation(n_components=2, min_history_days=200)
    assert method._state is None  # cold start
    out = method.compute(inp, session=object())
    # After fallback fit, state is populated and outputs come back.
    assert method._state is not None
    assert out, "expected non-empty output after fallback fit"
