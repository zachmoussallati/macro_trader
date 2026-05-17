"""Full-stack smoke (Stage 6 Phase 0.4).

Verifies the methods registry knows about every Stage-5 component
+ method. The signal-pipeline emission half is exercised by each
family's dedicated integration test (Stages 3/4A/4B/4C/5/6);
this smoke is the lightest check that the *registration* step
covers all 10 families.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from macro_trader.db.models.system import MethodRegistryRow
from macro_trader.methods.setup import register_all_methods

EXPECTED_METHOD_IDS: set[str] = {
    # Stage 2: data quality (registered in Stage 2 setup)
    "data_quality.zscore.v1",
    "data_quality.isoforest.v1",
    # Stage 3: trend / carry / value
    "trend.sma_short.v1",
    "trend.sma_medium.v1",
    "trend.sma_long.v1",
    "trend.ensemble.v1",
    "trend.hp_filter.v1",
    "carry.spot_proxy.v1",
    "value.zscore.v1",
    "value.cross_sectional.v1",
    # Stage 4A: positioning + dislocation
    "positioning.cot_zscore.v1",
    "positioning.cot_commercial.v1",
    "dislocation.pca.v1",
    "dislocation.dfm.v1",
    # Stage 4B: factor exposure + catalyst
    "factor_exposure.ols.v1",
    "factor_exposure.rf.v1",
    "catalyst.event_study.v1",
    # Stage 5: alt-data + nowcasting + vol surface
    "alt_data.eia_storage.v1",
    "alt_data.usda_wasde.v1",
    "alt_data.google_trends.v1",
    "nowcasting.ols_ar.v1",
    "nowcasting.bvar.v1",
    "vol_surface.raw.v1",
    "vol_surface.svi.v1",
}

EXPECTED_COMPONENTS: set[str] = {
    "data_quality",
    "trend_signal",
    "carry_signal",
    "value_signal",
    "positioning_signal",
    "dislocation_signal",
    "factor_exposure_signal",
    "catalyst_signal",
    "alt_data_signal",
    "nowcasting_signal",
    "vol_surface_signal",
}


@pytest.mark.integration
def test_full_signal_stack_registers(db_session) -> None:
    """After register_all_methods runs, every expected method_id is
    in the registry and every expected component is covered."""
    register_all_methods(db_session)
    db_session.flush()

    rows = list(db_session.scalars(select(MethodRegistryRow)))
    method_ids = {r.method_id for r in rows}
    components = {r.component for r in rows}

    missing_methods = EXPECTED_METHOD_IDS - method_ids
    assert not missing_methods, (
        f"missing method registrations: {sorted(missing_methods)}"
    )
    missing_components = EXPECTED_COMPONENTS - components
    assert not missing_components, (
        f"missing component registrations: {sorted(missing_components)}"
    )

    # Each expected component must have at least one baseline.
    for component in EXPECTED_COMPONENTS:
        component_rows = [r for r in rows if r.component == component]
        assert any(
            r.status.value == "baseline" for r in component_rows
        ), f"component {component!r} has no BASELINE method"


@pytest.mark.integration
def test_econml_gated_methods_register_only_when_econml_present(
    db_session,
) -> None:
    """factor_exposure.causal_forest.v1 + catalyst.causal.v1 register
    only when EconML is installed. Either-or assertion (don't fail
    when the [ml] extra isn't installed)."""
    from macro_trader.signals.catalyst.methods import _econml_available

    register_all_methods(db_session)
    db_session.flush()

    cf_present = bool(
        db_session.scalar(
            select(MethodRegistryRow).where(
                MethodRegistryRow.method_id == "factor_exposure.causal_forest.v1"
            )
        )
    )
    causal_present = bool(
        db_session.scalar(
            select(MethodRegistryRow).where(
                MethodRegistryRow.method_id == "catalyst.causal.v1"
            )
        )
    )
    expected = _econml_available()
    assert cf_present == expected, (
        f"factor_exposure.causal_forest.v1 registered={cf_present} "
        f"but EconML available={expected}"
    )
    assert causal_present == expected, (
        f"catalyst.causal.v1 registered={causal_present} "
        f"but EconML available={expected}"
    )
