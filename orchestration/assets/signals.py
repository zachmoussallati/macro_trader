"""Signal-family Dagster assets.

Each family is one asset that runs the family's daily runner. The
``daily_data_quality`` asset is upstream (per Stage 2 conventions) so
signals run after data + quality are settled.
"""

from __future__ import annotations

from dagster import (
    AssetExecutionContext,
    AssetIn,
    MaterializeResult,
    MetadataValue,
    asset,
)

from macro_trader.db.engine import get_sessionmaker
from macro_trader.signals.carry.runner import run_daily_carry
from macro_trader.signals.dislocation.refit import run_weekly_refit as run_dislocation_refit
from macro_trader.signals.dislocation.runner import run_daily_dislocation
from macro_trader.signals.factor_exposure.refit import (
    run_weekly_refit as run_factor_exposure_refit,
)
from macro_trader.signals.factor_exposure.runner import run_daily_factor_exposure
from macro_trader.signals.positioning.runner import run_daily_positioning
from macro_trader.signals.trend.runner import run_daily_trend
from macro_trader.signals.value.runner import run_daily_value


def _summarise(written: dict[str, int]) -> dict:
    return {
        "rows_written_total": MetadataValue.int(sum(written.values())),
        "per_method": MetadataValue.json(written),
    }


@asset(
    group_name="signals_trend",
    description="Daily trend signals (3 SMAs + ensemble + HP filter).",
    ins={
        "ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars"),
        "daily_data_quality": AssetIn(key="daily_data_quality"),
    },
)
def signal_trend(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
    daily_data_quality: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        written = run_daily_trend(session)
        session.commit()
    context.log.info(f"signals.trend.written={written}")
    return MaterializeResult(metadata=_summarise(written))


@asset(
    group_name="signals_carry",
    description="Daily carry signals (placeholder spot-proxy).",
    ins={
        "ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars"),
        "ingest_fred_series": AssetIn(key="ingest_fred_series"),
        "daily_data_quality": AssetIn(key="daily_data_quality"),
    },
)
def signal_carry(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
    ingest_fred_series: None,
    daily_data_quality: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        written = run_daily_carry(session)
        session.commit()
    context.log.info(f"signals.carry.written={written}")
    return MaterializeResult(metadata=_summarise(written))


@asset(
    group_name="signals_value",
    description="Daily value signals (z-score baseline + cross-sectional shadow).",
    ins={
        "ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars"),
        "daily_data_quality": AssetIn(key="daily_data_quality"),
    },
)
def signal_value(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
    daily_data_quality: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        written = run_daily_value(session)
        session.commit()
    context.log.info(f"signals.value.written={written}")
    return MaterializeResult(metadata=_summarise(written))


@asset(
    group_name="signals_positioning",
    description=(
        "Daily positioning signals (managed-money z-score baseline + "
        "commercial extremes shadow). Recomputes daily even when no new "
        "COT data has landed; metadata.is_fresh_data flags new reports."
    ),
    ins={
        "ingest_cftc_cot": AssetIn(key="ingest_cftc_cot"),
        "daily_data_quality": AssetIn(key="daily_data_quality"),
    },
)
def signal_positioning(
    context: AssetExecutionContext,
    ingest_cftc_cot: None,
    daily_data_quality: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        written = run_daily_positioning(session)
        session.commit()
    context.log.info(f"signals.positioning.written={written}")
    return MaterializeResult(metadata=_summarise(written))


@asset(
    group_name="signals_dislocation",
    description=(
        "Weekly refit (Sunday 00:00 UTC) of PCA + DFM dislocation "
        "models. Persists fitted state to "
        "system.methods_registry.serialized_blob; daily inference "
        "(signal_dislocation) reads it. PCA components are sign-aligned "
        "to the prior week's loadings so dashboard heatmaps stay "
        "continuous across refits."
    ),
    ins={
        "ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars"),
    },
)
def dislocation_models_refit(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        results = run_dislocation_refit(session)
        session.commit()
    sizes = {r.method_id: r.blob_size_bytes for r in results}
    context.log.info(f"signals.dislocation.refit blob_sizes={sizes}")
    return MaterializeResult(
        metadata={
            "n_methods_refit": MetadataValue.int(len(results)),
            "blob_sizes_bytes": MetadataValue.json(sizes),
            "explained_variance": MetadataValue.json(
                {r.method_id: r.explained_variance for r in results}
            ),
        }
    )


@asset(
    group_name="signals_dislocation",
    description=(
        "Daily cross-asset dislocation signals (PCA baseline + DFM "
        "shadow). Reads fitted state from the weekly "
        "dislocation_models_refit asset; falls back to fitting on the "
        "daily window with a logged warning if no state exists."
    ),
    ins={
        "ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars"),
        "daily_data_quality": AssetIn(key="daily_data_quality"),
        "dislocation_models_refit": AssetIn(key="dislocation_models_refit"),
    },
)
def signal_dislocation(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
    daily_data_quality: None,
    dislocation_models_refit: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        written = run_daily_dislocation(session)
        session.commit()
    context.log.info(f"signals.dislocation.written={written}")
    return MaterializeResult(metadata=_summarise(written))


@asset(
    group_name="signals_factor_exposure",
    description=(
        "Weekly refit (Sunday 01:00 UTC) of OLS + RF + (optional) "
        "Causal Forest factor exposure models. Persists fitted state to "
        "system.methods_registry.serialized_blob. Causal Forest is "
        "skipped silently when the [ml] extra (EconML) is not installed."
    ),
    ins={
        "ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars"),
        "ingest_fred_series": AssetIn(key="ingest_fred_series"),
    },
)
def factor_exposure_models_refit(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
    ingest_fred_series: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        results = run_factor_exposure_refit(session)
        session.commit()
    sizes = {r.method_id: r.blob_size_bytes for r in results}
    context.log.info(f"signals.factor_exposure.refit blob_sizes={sizes}")
    return MaterializeResult(
        metadata={
            "n_methods_refit": MetadataValue.int(len(results)),
            "blob_sizes_bytes": MetadataValue.json(sizes),
            "instruments_per_method": MetadataValue.json(
                {r.method_id: r.instruments for r in results}
            ),
        }
    )


@asset(
    group_name="signals_factor_exposure",
    description=(
        "Daily factor exposure signals (OLS baseline + RF shadow + "
        "optional Causal Forest shadow). Reads fitted state from the "
        "weekly factor_exposure_models_refit asset; falls back to "
        "fitting on the daily window if no state exists."
    ),
    ins={
        "ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars"),
        "ingest_fred_series": AssetIn(key="ingest_fred_series"),
        "daily_data_quality": AssetIn(key="daily_data_quality"),
        "factor_exposure_models_refit": AssetIn(key="factor_exposure_models_refit"),
    },
)
def signal_factor_exposure(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
    ingest_fred_series: None,
    daily_data_quality: None,
    factor_exposure_models_refit: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        written = run_daily_factor_exposure(session)
        session.commit()
    context.log.info(f"signals.factor_exposure.written={written}")
    return MaterializeResult(metadata=_summarise(written))


SIGNAL_ASSETS = [
    signal_trend,
    signal_carry,
    signal_value,
    signal_positioning,
    dislocation_models_refit,
    signal_dislocation,
    factor_exposure_models_refit,
    signal_factor_exposure,
]
