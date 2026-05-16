"""Causal Forest factor exposure tests (gated on EconML install)."""

from __future__ import annotations

import pytest

from macro_trader.signals.factor_exposure.methods import _econml_available

pytestmark = pytest.mark.skipif(
    not _econml_available(), reason="EconML not installed; run `uv sync --extra ml`"
)


@pytest.mark.unit
def test_cf_metadata() -> None:
    from macro_trader.signals.factor_exposure.methods import (
        CausalForestFactorExposure,
    )

    m = CausalForestFactorExposure()
    assert m.metadata.method_id == "factor_exposure.causal_forest.v1"


@pytest.mark.unit
def test_cf_constructor_raises_when_econml_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The method's constructor must check for EconML and raise a
    clear error if missing — not at use-time, so the failure is loud."""
    from macro_trader.signals.factor_exposure import methods

    monkeypatch.setattr(methods, "_econml_available", lambda: False)
    with pytest.raises(RuntimeError, match=r"\[ml\]"):
        methods.CausalForestFactorExposure()


# Stage 4C: with EconML installed, exercise the CausalForestDML fit
# end-to-end on a small synthetic panel to confirm the existing
# Stage 4B implementation actually converges + persists.


@pytest.mark.unit
def test_cf_fits_on_synthetic_panel_and_emits_state() -> None:
    """One CausalForest per (instrument, factor); even a tiny panel
    should produce a non-empty state and per-instrument CATE entries."""
    import numpy as np
    import pandas as pd

    from macro_trader.signals.factor_exposure.methods import (
        CausalForestFactorExposure,
    )

    rng = np.random.default_rng(0)
    n_days = 320
    n_instruments = 3
    n_factors = 2
    factors = rng.normal(scale=1.0, size=(n_days, n_factors))
    betas = rng.normal(scale=0.5, size=(n_instruments, n_factors))
    returns = factors @ betas.T + rng.normal(scale=0.001, size=(n_days, n_instruments))
    idx = pd.date_range("2024-01-01", periods=n_days, freq="B", tz="UTC")
    rdf = pd.DataFrame(
        returns, index=idx, columns=[f"I{i}" for i in range(n_instruments)]
    )
    fdf = pd.DataFrame(
        factors, index=idx, columns=[f"F{j}" for j in range(n_factors)]
    )

    # n_estimators must be divisible by EconML's subforest_size (4).
    method = CausalForestFactorExposure(
        lookback_days=300, min_history_days=200, n_estimators=32
    )
    method.fit_on_panels(rdf, fdf)
    assert method._state is not None
    for inst, fit in method._state["per_instrument"].items():
        assert "cates" in fit
        for factor_name in fdf.columns:
            assert factor_name in fit["cates"]
            assert np.isfinite(fit["cates"][factor_name])
        _ = inst
