"""Composite scoring Dagster assets (Stage 7).

Three assets:

- ``composite_weights_refit`` — weekly Sunday 06:00 UTC. Reads the
  attribution table and writes a new ``signals.composite_weights``
  snapshot for ``composite.linear.v1`` (the production composite).
- ``composite_bayesian_refit`` — weekly Sunday 06:30 UTC. Fits the
  three-level NIG hierarchy and stores the fitted state on
  ``composite.bayesian_hier.v1`` in ``system.methods_registry``.
- ``composite_gbm_refit`` — quarterly (first Sunday of Jan/Apr/Jul/
  Oct at 07:00 UTC). Trains LightGBM on the multi-year panel.
- ``composite_score_compute`` — daily 23:45 UTC (15 min after
  regime_classification). Runs every composite method's compute()
  and persists rows to ``signals.composite_scores`` + comparator
  results to ``system.method_comparisons``.
"""

from dagster import (
    AssetExecutionContext,
    AssetIn,
    MaterializeResult,
    MetadataValue,
    asset,
)

from macro_trader.composite.refit import (
    refit_bayesian_hierarchical,
    refit_gbm,
    refit_weights,
)
from macro_trader.composite.runner import run_daily_composite
from macro_trader.db.engine import get_sessionmaker


@asset(
    group_name="composite",
    description=(
        "Weekly refit of the composite weight snapshot from the "
        "regime attribution table (Sunday 06:00 UTC). Persists one "
        "row per (regime_label, signal_method) into "
        "signals.composite_weights with the latest snapshot_ts."
    ),
    ins={
        "regime_attribution_compute": AssetIn(key="regime_attribution_compute"),
    },
)
def composite_weights_refit(
    context: AssetExecutionContext,
    regime_attribution_compute: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        n = refit_weights(session)
        session.commit()
    context.log.info(f"composite.weights_refit rows={n}")
    return MaterializeResult(metadata={"weight_rows": MetadataValue.int(n)})


@asset(
    group_name="composite",
    description=(
        "Weekly fit of the Bayesian hierarchical composite "
        "(Sunday 06:30 UTC). Three-level NIG-style shrinkage; "
        "fitted state persisted to system.methods_registry."
    ),
    ins={
        "composite_weights_refit": AssetIn(key="composite_weights_refit"),
    },
)
def composite_bayesian_refit(
    context: AssetExecutionContext,
    composite_weights_refit: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        result = refit_bayesian_hierarchical(session)
        session.commit()
    if result is None:
        context.log.info("composite.bayesian_refit no_state")
        return MaterializeResult(
            metadata={"blob_size_bytes": MetadataValue.int(0)}
        )
    return MaterializeResult(
        metadata={
            "blob_size_bytes": MetadataValue.int(result.blob_size_bytes),
            "fit_rows": MetadataValue.int(result.fit_rows),
            "compressed": MetadataValue.bool(result.compressed),
        }
    )


@asset(
    group_name="composite",
    description=(
        "Quarterly fit of the LightGBM composite (first Sunday of "
        "Jan/Apr/Jul/Oct at 07:00 UTC). Trains a regression of "
        "next-day log return on the (signals * regime probabilities * "
        "instrument one-hot) feature panel."
    ),
    ins={
        "composite_weights_refit": AssetIn(key="composite_weights_refit"),
    },
)
def composite_gbm_refit(
    context: AssetExecutionContext,
    composite_weights_refit: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        result = refit_gbm(session)
        session.commit()
    if result is None:
        context.log.info("composite.gbm_refit no_state")
        return MaterializeResult(
            metadata={"blob_size_bytes": MetadataValue.int(0)}
        )
    return MaterializeResult(
        metadata={
            "blob_size_bytes": MetadataValue.int(result.blob_size_bytes),
            "fit_rows": MetadataValue.int(result.fit_rows),
            "compressed": MetadataValue.bool(result.compressed),
        }
    )


@asset(
    group_name="composite",
    description=(
        "Daily composite scoring across all 3 methods (linear + "
        "bayesian_hier + gbm). Runs 15 min after the daily signals "
        "and regime classification at 23:30 UTC, so it reads fresh "
        "signal_values + regime_states. Persists rows to "
        "signals.composite_scores; cross-method comparator persists "
        "to system.method_comparisons."
    ),
    ins={
        "regime_classification": AssetIn(key="regime_classification"),
        "signal_trend": AssetIn(key="signal_trend"),
        "signal_carry": AssetIn(key="signal_carry"),
        "signal_value": AssetIn(key="signal_value"),
        "signal_positioning": AssetIn(key="signal_positioning"),
        "signal_dislocation": AssetIn(key="signal_dislocation"),
        "signal_factor_exposure": AssetIn(key="signal_factor_exposure"),
        "signal_catalyst": AssetIn(key="signal_catalyst"),
        "signal_alt_data": AssetIn(key="signal_alt_data"),
        "signal_nowcasting": AssetIn(key="signal_nowcasting"),
        "signal_vol_surface": AssetIn(key="signal_vol_surface"),
    },
)
def composite_score_compute(
    context: AssetExecutionContext,
    regime_classification: None,
    signal_trend: None,
    signal_carry: None,
    signal_value: None,
    signal_positioning: None,
    signal_dislocation: None,
    signal_factor_exposure: None,
    signal_catalyst: None,
    signal_alt_data: None,
    signal_nowcasting: None,
    signal_vol_surface: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        written = run_daily_composite(session)
        session.commit()
    context.log.info(f"composite.daily.written={written}")
    return MaterializeResult(
        metadata={
            "rows_written_total": MetadataValue.int(sum(written.values())),
            "per_method": MetadataValue.json(written),
        }
    )


COMPOSITE_ASSETS = [
    composite_weights_refit,
    composite_bayesian_refit,
    composite_gbm_refit,
    composite_score_compute,
]
