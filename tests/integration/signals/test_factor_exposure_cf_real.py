"""Real-data validation for ``factor_exposure.causal_forest.v1``.

Gated on the ``real_data`` marker; requires the
``backfill_panel`` fixture which loads
``tests/data/backfill_504d.parquet``. When the cache is absent
every test in this module skips with the standard "backfill
cache not found" message.

Validation contract (per Stage 4C prompt + Stage 5 Phase 0.3):

- CausalForest CATE estimates differ from OLS β for at least 30%
  of (instrument, factor) pairs (abs relative diff > 0.25).
- Serialization round-trip preserves predictions exactly on the
  same input.
- Fit produces a populated ``_state`` for every instrument that
  has sufficient data in the backfill (the inner-join +
  dropna(how=any) means an instrument with sparse history shrinks
  the window for everyone, so "every instrument" is the right
  bar only when no instrument is sparse — assert the more general
  ">=80% of instruments fit").
"""

from __future__ import annotations

import pickle

import numpy as np
import pytest

pytestmark = pytest.mark.real_data


def test_factor_exposure_cf_cates_differ_from_ols(backfill_panel) -> None:
    """Fit both OLS and CF on the backfill; assert CF CATEs differ
    materially from OLS β for at least 30% of (instrument, factor)
    pairs."""
    from macro_trader.signals.factor_exposure.methods import (
        CausalForestFactorExposure,
        OLSFactorExposure,
    )

    returns = np.log(backfill_panel.bars.replace(0, np.nan)).diff().dropna(how="all")
    factor_panel = backfill_panel.factors
    if returns.empty or factor_panel.empty:
        pytest.skip("backfill missing bars or factors")

    ols = OLSFactorExposure(lookback_days=252, min_history_days=200)
    cf = CausalForestFactorExposure(lookback_days=504, min_history_days=300, n_estimators=200)

    ols.fit_on_panels(returns, factor_panel)
    cf.fit_on_panels(returns, factor_panel)

    assert ols._state is not None, "OLS failed to fit on backfill"
    assert cf._state is not None, "CF failed to fit on backfill"

    n_pairs = 0
    n_differ = 0
    for inst, ols_fit in ols._state["per_instrument"].items():
        cf_fit = cf._state["per_instrument"].get(inst)
        if cf_fit is None:
            continue
        for factor_name, beta in ols_fit["betas"].items():
            cate = cf_fit["cates"].get(factor_name)
            if cate is None:
                continue
            n_pairs += 1
            denom = max(abs(beta), 1e-6)
            if abs(cate - beta) / denom > 0.25:
                n_differ += 1

    assert n_pairs > 0, "no comparable (instrument, factor) pairs"
    frac = n_differ / n_pairs
    assert frac >= 0.30, (
        f"only {frac:.0%} of pairs differ materially; expected >=30%"
    )


def test_factor_exposure_cf_serialize_round_trip(backfill_panel) -> None:
    """Predicted CATEs after serialize/deserialize round-trip exactly."""
    from macro_trader.signals.factor_exposure.methods import (
        CausalForestFactorExposure,
    )

    returns = np.log(backfill_panel.bars.replace(0, np.nan)).diff().dropna(how="all")
    factor_panel = backfill_panel.factors
    if returns.empty or factor_panel.empty:
        pytest.skip("backfill missing bars or factors")

    cf = CausalForestFactorExposure(
        lookback_days=504, min_history_days=300, n_estimators=200
    )
    cf.fit_on_panels(returns, factor_panel)
    if cf._state is None:
        pytest.skip("CF did not converge on this backfill")

    blob = cf.serialize()
    assert blob, "fitted CF serialised to empty blob"

    restored = CausalForestFactorExposure.deserialize(blob)
    z = dict.fromkeys(factor_panel.columns, 0.5)
    assert cf.predict_at(z) == restored.predict_at(z)


def test_factor_exposure_cf_fits_for_majority_of_instruments(backfill_panel) -> None:
    """At least 80% of instruments produce a fitted CATE entry."""
    from macro_trader.signals.factor_exposure.methods import (
        CausalForestFactorExposure,
    )

    returns = np.log(backfill_panel.bars.replace(0, np.nan)).diff().dropna(how="all")
    factor_panel = backfill_panel.factors
    if returns.empty or factor_panel.empty:
        pytest.skip("backfill missing bars or factors")

    cf = CausalForestFactorExposure(
        lookback_days=504, min_history_days=300, n_estimators=200
    )
    cf.fit_on_panels(returns, factor_panel)
    assert cf._state is not None

    fitted = set(cf._state["per_instrument"].keys())
    total = set(returns.columns)
    coverage = len(fitted) / max(len(total), 1)
    assert coverage >= 0.80, (
        f"CF fitted only {coverage:.0%} of instruments; expected >=80%"
    )
    _ = pickle  # silence unused import; pickle re-exported for downstream tools
