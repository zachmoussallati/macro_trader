"""Factor construction tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest

from macro_trader.signals.factor_exposure.factors import (
    DEFAULT_FACTORS,
    FactorSpec,
    _apply_transform,
    _rolling_zscore,
    build_factor_panel,
    latest_factor_zscores,
)


@pytest.mark.unit
def test_apply_transform_yoy_change_returns_pct_change_252() -> None:
    s = pd.Series(np.linspace(100, 200, 600))
    out = _apply_transform(s, "yoy_change")
    assert (out > 0).all()
    assert len(out) == len(s) - 252


@pytest.mark.unit
def test_apply_transform_60d_change_inverted_flips_sign() -> None:
    s = pd.Series(np.arange(200, dtype=float))
    out = _apply_transform(s, "60d_change_inverted")
    assert (out < 0).all()


@pytest.mark.unit
def test_apply_transform_level_inverted_negates() -> None:
    s = pd.Series([1.0, 2.0, 3.0])
    out = _apply_transform(s, "level_inverted")
    assert list(out) == [-1.0, -2.0, -3.0]


@pytest.mark.unit
def test_apply_transform_unknown_raises() -> None:
    with pytest.raises(ValueError):
        _apply_transform(pd.Series([1.0]), "not_a_real_transform")  # type: ignore[arg-type]


@pytest.mark.unit
def test_rolling_zscore_centered_around_zero_for_iid() -> None:
    rng = np.random.default_rng(0)
    s = pd.Series(rng.normal(size=500))
    z = _rolling_zscore(s, window=252, min_periods=60)
    tail = z.iloc[300:].mean()
    assert abs(float(tail)) < 0.5


@pytest.mark.unit
def test_default_factors_list_has_six_entries() -> None:
    assert len(DEFAULT_FACTORS) == 6
    names = {f.name for f in DEFAULT_FACTORS}
    assert names == {"growth", "inflation", "liquidity", "usd", "oil", "risk_on"}
    for f in DEFAULT_FACTORS:
        assert isinstance(f, FactorSpec)
        assert f.fred_series.startswith("FRED:")


@pytest.mark.unit
def test_build_factor_panel_returns_empty_when_loaders_return_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If every FRED series load returns empty, the panel is empty."""

    def _empty_loader(
        session: Any, series_id: str, *, start: datetime, end: datetime, as_of: datetime | None = None
    ) -> pd.Series:
        return pd.Series(dtype=float, name=series_id)

    monkeypatch.setattr(
        "macro_trader.signals.factor_exposure.factors.load_macro_series", _empty_loader
    )
    panel = build_factor_panel(session=object(), as_of=datetime(2024, 6, 1))
    assert panel.empty


@pytest.mark.unit
def test_build_factor_panel_with_synthetic_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loader that returns 5 years of data per series produces a
    populated panel; the latest_factor_zscores helper picks the last
    finite z per factor."""
    rng = np.random.default_rng(0)

    def _loader(
        session: Any, series_id: str, *, start: datetime, end: datetime, as_of: datetime | None = None
    ) -> pd.Series:
        # ~5 years of daily data, slight positive drift for INDPRO/CPI etc.
        idx = pd.date_range("2020-01-01", "2024-12-31", freq="B", tz="UTC")
        values = 100.0 + rng.normal(scale=1.0, size=len(idx)).cumsum() * 0.1
        return pd.Series(values, index=idx, name=series_id)

    monkeypatch.setattr(
        "macro_trader.signals.factor_exposure.factors.load_macro_series", _loader
    )
    as_of = datetime(2024, 12, 30)
    panel = build_factor_panel(session=object(), as_of=as_of, lookback_days=252)
    assert not panel.empty
    assert set(panel.columns) <= {f.name for f in DEFAULT_FACTORS}

    z = latest_factor_zscores(session=object(), as_of=as_of)
    assert z, "expected at least one factor with a finite z-score"
    for v in z.values():
        assert not np.isnan(v) and not np.isinf(v)
