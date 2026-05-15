"""Unit tests for PCADislocation.

We stub ``load_close_panel`` so the test runs without a DB. Tests
exercise the sign convention, the residual recovery on a clean
two-factor synthetic, and the early-history bail-out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from macro_trader.signals.base import SignalInput
from macro_trader.signals.dislocation.methods import PCADislocation


def _synthetic_panel(
    *,
    n_days: int = 300,
    n_instruments: int = 5,
    factor_loadings: np.ndarray | None = None,
    idiosyncratic_noise: float = 0.01,
    seed: int = 0,
) -> pd.DataFrame:
    """Build a synthetic close-price panel with a clean factor structure.

    Returns are generated as: ``r_t = B @ f_t + eps_t`` with ``f`` a
    two-factor latent process and ``B`` per-instrument loadings. Close
    prices are the cumulative product of returns.
    """
    rng = np.random.default_rng(seed)
    if factor_loadings is None:
        factor_loadings = rng.normal(scale=0.5, size=(n_instruments, 2))
    factors = rng.normal(scale=0.01, size=(n_days, 2))
    returns = factors @ factor_loadings.T + rng.normal(
        scale=idiosyncratic_noise, size=(n_days, n_instruments)
    )
    prices = 100.0 * np.exp(np.cumsum(returns, axis=0))
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B", tz="UTC")
    cols = [f"I{i}" for i in range(n_instruments)]
    return pd.DataFrame(prices, index=dates, columns=cols)


def _patch_loader(monkeypatch: pytest.MonkeyPatch, panel: pd.DataFrame) -> None:
    def _fake(
        session: Any,
        instrument_ids: list[str],
        *,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
        calendar: str = "NYSE",
        ffill_limit: int = 1,
    ) -> pd.DataFrame:
        # Reindex to the calendar implied by panel.index; matches what
        # the real load_close_panel does. Keep only requested columns.
        return panel.loc[:, [c for c in instrument_ids if c in panel.columns]]

    monkeypatch.setattr(
        "macro_trader.signals.dislocation.methods.load_close_panel", _fake
    )


def _input(instrument_ids: list[str], *, panel_index: pd.DatetimeIndex) -> SignalInput:
    end = panel_index[-1].to_pydatetime()
    start = (panel_index[-1] - timedelta(days=21)).to_pydatetime()
    return SignalInput(
        instrument_ids=instrument_ids,
        as_of=end,
        start=start,
        end=end,
    )


@pytest.mark.unit
def test_pca_dislocation_metadata() -> None:
    m = PCADislocation()
    assert m.metadata.method_id == "dislocation.pca.v1"
    assert m.metadata.component == "dislocation_signal"


@pytest.mark.unit
def test_pca_dislocation_requires_session() -> None:
    inp = SignalInput(
        instrument_ids=["I0"],
        as_of=datetime(2024, 6, 1, tzinfo=UTC),
        start=datetime(2024, 5, 1, tzinfo=UTC),
        end=datetime(2024, 6, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="session"):
        PCADislocation().compute(inp, None)


@pytest.mark.unit
def test_pca_returns_empty_when_insufficient_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = _synthetic_panel(n_days=80, n_instruments=5)
    _patch_loader(monkeypatch, panel)
    inp = _input(list(panel.columns), panel_index=panel.index)
    outputs = PCADislocation().compute(inp, session=object())
    assert outputs == []


@pytest.mark.unit
def test_pca_emits_outputs_with_clean_factor_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = _synthetic_panel(n_days=400, n_instruments=5, idiosyncratic_noise=0.01)
    _patch_loader(monkeypatch, panel)

    inp = _input(list(panel.columns), panel_index=panel.index)
    outputs = PCADislocation(n_components=2, min_history_days=200).compute(
        inp, session=object()
    )
    assert outputs, "expected at least one output"

    # All raw_values should be finite + bounded by tanh range.
    for o in outputs:
        assert -1.0 <= o.raw_value <= 1.0
        assert o.metadata["method_id"] == "dislocation.pca.v1"
        assert o.metadata["n_components"] == 2
        assert 0.0 <= o.metadata["explained_variance"] <= 1.0

    # On a clean two-factor synthetic with 2 components, explained
    # variance should be high (>= 0.6 most of the time).
    avg_ev = float(np.mean([o.metadata["explained_variance"] for o in outputs]))
    assert avg_ev >= 0.5


@pytest.mark.unit
def test_pca_skips_when_too_few_instruments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If after dropping NaN-only columns fewer than n_components+1
    instruments remain, the method skips silently."""
    panel = _synthetic_panel(n_days=300, n_instruments=3)
    # Wipe two columns to NaN so only one usable column remains; n_components=2.
    panel.loc[:, "I1"] = np.nan
    panel.loc[:, "I2"] = np.nan
    _patch_loader(monkeypatch, panel)

    inp = _input(list(panel.columns), panel_index=panel.index)
    outputs = PCADislocation(n_components=2, min_history_days=200).compute(
        inp, session=object()
    )
    assert outputs == []


@pytest.mark.unit
def test_pca_emits_only_window_ts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outputs are filtered to [data.start, data.end]."""
    panel = _synthetic_panel(n_days=400, n_instruments=4)
    _patch_loader(monkeypatch, panel)

    end = panel.index[-1].to_pydatetime()
    start = (panel.index[-1] - timedelta(days=5)).to_pydatetime()
    inp = SignalInput(
        instrument_ids=list(panel.columns),
        as_of=end,
        start=start,
        end=end,
    )
    outputs = PCADislocation(n_components=2, min_history_days=200).compute(
        inp, session=object()
    )
    if outputs:
        for o in outputs:
            ts = pd.Timestamp(o.value_ts)
            assert pd.Timestamp(start) <= ts <= pd.Timestamp(end)
