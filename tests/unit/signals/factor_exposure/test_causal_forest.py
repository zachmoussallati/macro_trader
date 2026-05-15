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


# A real fit test would need synthetic data and a few seconds of CPU.
# That's deferred to integration tests against real-data backfills
# (Stage 9 backtester is the right place — see notes/stage_4b/tradeoffs.md).
