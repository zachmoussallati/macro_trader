"""Regime classifier method tests (metadata + rules + BOCPD math)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from macro_trader.regime import NAMED_REGIMES
from macro_trader.regime.methods import (
    BOCPDRegimeClassifier,
    GMMRegimeClassifier,
    MSVARRegimeClassifier,
    RegimeInput,
    RulesRegimeClassifier,
    _hmmlearn_available,
)


@pytest.mark.unit
def test_named_regimes_has_five_entries() -> None:
    assert len(NAMED_REGIMES) == 5
    assert set(NAMED_REGIMES) == {
        "risk_on_growth",
        "risk_off_defensive",
        "stagflation",
        "carry_friendly",
        "vol_spike",
    }


@pytest.mark.unit
def test_rules_metadata() -> None:
    m = RulesRegimeClassifier()
    assert m.metadata.method_id == "regime.rules.v1"
    assert m.metadata.component == "regime_classifier"


@pytest.mark.unit
def test_rules_classify_vol_spike() -> None:
    row = pd.Series(
        {
            "vix_level": 35.0,
            "realized_vol_60d": 0.30,
            "growth": 0.0,
            "inflation": 0.0,
            "usd": 0.0,
            "yield_curve_slope": 0.5,
        }
    )
    label, vec = RulesRegimeClassifier().classify_row(row)
    assert label == "vol_spike"
    assert vec["vol_spike"] == 1.0
    assert sum(vec.values()) == 1.0


@pytest.mark.unit
def test_rules_classify_stagflation() -> None:
    row = pd.Series(
        {"vix_level": 22.0, "realized_vol_60d": 0.18, "growth": 0.8, "inflation": 1.2,
         "usd": 0.0, "yield_curve_slope": 0.5}
    )
    label, _ = RulesRegimeClassifier().classify_row(row)
    assert label == "stagflation"


@pytest.mark.unit
def test_rules_classify_carry_friendly_default() -> None:
    row = pd.Series(
        {"vix_level": 12.0, "realized_vol_60d": 0.10, "growth": 0.0, "inflation": 0.0,
         "usd": 0.2, "yield_curve_slope": 1.2}
    )
    label, _ = RulesRegimeClassifier().classify_row(row)
    assert label == "carry_friendly"


@pytest.mark.unit
def test_rules_requires_session() -> None:
    inp = RegimeInput(as_of=datetime(2024, 12, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="session"):
        RulesRegimeClassifier().compute(inp, None)


@pytest.mark.unit
def test_gmm_metadata_and_unfit_serialize_empty() -> None:
    m = GMMRegimeClassifier()
    assert m.metadata.method_id == "regime.gmm.v1"
    assert m.serialize() == b""


@pytest.mark.unit
def test_msvar_metadata() -> None:
    m = MSVARRegimeClassifier()
    assert m.metadata.method_id == "regime.msvar.v1"
    assert m.metadata.component == "regime_classifier"


@pytest.mark.skipif(
    not _hmmlearn_available(), reason="hmmlearn not installed"
)
@pytest.mark.unit
def test_hmm_metadata() -> None:
    from macro_trader.regime.methods import HMMRegimeClassifier

    m = HMMRegimeClassifier()
    assert m.metadata.method_id == "regime.hmm.v1"


@pytest.mark.unit
def test_bocpd_metadata() -> None:
    m = BOCPDRegimeClassifier()
    assert m.metadata.method_id == "regime.bocpd.v1"


@pytest.mark.unit
def test_bocpd_changepoint_spikes_at_known_break() -> None:
    """Build a series with a clean mean-shift halfway through; BOCPD's
    changepoint probability should spike sharply near the break."""
    rng = np.random.default_rng(42)
    n = 200
    x = np.concatenate([
        rng.normal(loc=0.0, scale=1.0, size=n // 2),
        rng.normal(loc=5.0, scale=1.0, size=n // 2),
    ])
    method = BOCPDRegimeClassifier(hazard_lambda=60)
    cp = method._run_bocpd(x)
    # The break is at index n//2; the spike should be in the next few
    # observations (the run-length posterior takes a few steps to react).
    pre_break = cp[: n // 2 - 5]
    post_break = cp[n // 2 - 1 : n // 2 + 10]
    assert post_break.max() > pre_break.max(), (
        f"post_break max={post_break.max():.3f} did not exceed pre={pre_break.max():.3f}"
    )
    # And the post-break max should be meaningfully above the prior
    # baseline.
    assert post_break.max() > 0.05


@pytest.mark.unit
def test_bocpd_changepoint_low_on_stationary_series() -> None:
    """No real changepoint -> per-step changepoint probability stays
    bounded (the prior hazard rate 1/60 sets a floor)."""
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    cp = BOCPDRegimeClassifier(hazard_lambda=60)._run_bocpd(x)
    assert cp.mean() < 0.10  # average probability should stay low
