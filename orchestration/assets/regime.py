"""Regime-classifier Dagster assets (Stage 6).

Three assets:

- ``regime_models_refit`` — weekly Sunday 04:00 UTC. Re-fits GMM
  unconditionally and HMM/MS-VAR if the quarterly flag fires
  (Stage 6 keeps quarterly via a separate cron schedule).
- ``regime_classification`` — daily 23:30 UTC. Reads cached state
  + runs all 5 methods, persists to ``regime.regime_states``.
- ``regime_attribution_compute`` — weekly Sunday 05:00 UTC.
  Populates ``regime.regime_attribution`` from the regime states +
  signal_values + daily_bars panels.
"""

from dagster import (
    AssetExecutionContext,
    AssetIn,
    MaterializeResult,
    MetadataValue,
    asset,
)

from macro_trader.db.engine import get_sessionmaker
from macro_trader.regime.attribution import compute_attribution
from macro_trader.regime.refit import run_weekly_refit as run_regime_refit
from macro_trader.regime.runner import run_daily_regime_classification


@asset(
    group_name="regime",
    description=(
        "Weekly refit of GMM (+ HMM/MS-VAR when quarterly cron fires "
        "via separate include_quarterly param) regime classifiers. "
        "Persists fitted state to system.methods_registry.serialized_blob."
    ),
    ins={
        "ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars"),
        "ingest_fred_series": AssetIn(key="ingest_fred_series"),
    },
)
def regime_models_refit(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
    ingest_fred_series: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        # GMM unconditional weekly; quarterly methods (HMM, MS-VAR)
        # toggled by partition / cron in production. Default to
        # include_quarterly=True for first deploy so the quarterly
        # methods land fitted state without a separate trigger.
        results = run_regime_refit(session, include_quarterly=True)
        session.commit()
    sizes = {r.method_id: r.blob_size_bytes for r in results}
    context.log.info(f"regime.refit blob_sizes={sizes}")
    return MaterializeResult(
        metadata={
            "n_methods_refit": MetadataValue.int(len(results)),
            "blob_sizes_bytes": MetadataValue.json(sizes),
            "fit_rows": MetadataValue.json(
                {r.method_id: r.fit_rows for r in results}
            ),
        }
    )


@asset(
    group_name="regime",
    description=(
        "Daily regime classification across all 5 methods (rules + "
        "GMM + HMM + MS-VAR + BOCPD). Persists one row per (method, "
        "value_ts) to regime.regime_states."
    ),
    ins={
        "ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars"),
        "ingest_fred_series": AssetIn(key="ingest_fred_series"),
        "regime_models_refit": AssetIn(key="regime_models_refit"),
        "daily_data_quality": AssetIn(key="daily_data_quality"),
    },
)
def regime_classification(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
    ingest_fred_series: None,
    regime_models_refit: None,
    daily_data_quality: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        written = run_daily_regime_classification(session)
        session.commit()
    context.log.info(f"regime.classification.written={written}")
    return MaterializeResult(
        metadata={
            "rows_written_total": MetadataValue.int(sum(written.values())),
            "per_method": MetadataValue.json(written),
        }
    )


@asset(
    group_name="regime",
    description=(
        "Weekly attribution compute (Sunday 05:00 UTC). For each "
        "(regime_method, regime_label, signal_method) triple, "
        "computes mean_return / sharpe / hit_rate over the trailing "
        "year and persists to regime.regime_attribution."
    ),
    ins={
        "regime_classification": AssetIn(key="regime_classification"),
    },
)
def regime_attribution_compute(
    context: AssetExecutionContext,
    regime_classification: None,
) -> MaterializeResult:
    from sqlalchemy import select

    from macro_trader.db.models.system import MethodRegistryRow
    from macro_trader.utils.dates import utcnow

    session_factory = get_sessionmaker()
    written: dict[str, int] = {}
    with session_factory() as session:
        regime_methods = [
            r.method_id
            for r in session.scalars(
                select(MethodRegistryRow).where(
                    MethodRegistryRow.component == "regime_classifier"
                )
            )
        ]
        signal_method_ids = [
            r.method_id
            for r in session.scalars(
                select(MethodRegistryRow).where(
                    MethodRegistryRow.component.like("%_signal")
                )
            )
        ]
        for regime_method_id in regime_methods:
            rows = compute_attribution(
                session,
                regime_method_id=regime_method_id,
                signal_method_ids=signal_method_ids,
                as_of=utcnow(),
            )
            written[regime_method_id] = len(rows)
        session.commit()
    context.log.info(f"regime.attribution.written={written}")
    return MaterializeResult(
        metadata={
            "regime_methods": MetadataValue.int(len(written)),
            "rows_per_method": MetadataValue.json(written),
        }
    )


REGIME_ASSETS = [
    regime_models_refit,
    regime_classification,
    regime_attribution_compute,
]
