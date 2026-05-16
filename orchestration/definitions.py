"""Dagster definitions root.

Assets, jobs, schedules, and resources Dagster discovers when launched via
``dagster dev -f orchestration/definitions.py``.

On code-location startup this module calls
:func:`macro_trader.methods.setup.register_all_methods` so the methods
registry is rebuilt from code every time Dagster boots. Idempotent.
"""

from __future__ import annotations

from dagster import (
    AssetSelection,
    Definitions,
    ScheduleDefinition,
    define_asset_job,
)

from macro_trader.config import get_settings
from macro_trader.db.engine import get_session
from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import get_default_registry
from macro_trader.methods.setup import register_all_methods
from orchestration.assets import (
    ALL_ASSETS,
    catalyst_models_refit,
    daily_data_quality,
    dislocation_models_refit,
    factor_exposure_models_refit,
    heartbeat_asset,
    ingest_cftc_cot,
    ingest_eia,
    ingest_fred_series,
    ingest_google_trends,
    ingest_noaa_weather,
    ingest_usda,
    ingest_yfinance_bars,
    nowcasting_models_refit,
    refresh_calendar_events,
    signal_alt_data,
    signal_carry,
    signal_catalyst,
    signal_dislocation,
    signal_factor_exposure,
    signal_nowcasting,
    signal_positioning,
    signal_trend,
    signal_value,
    signal_vol_surface,
)

log = get_logger(__name__)


def _bootstrap_methods_registry() -> None:
    """Idempotent method registration on code-location startup."""
    try:
        with get_session() as session:
            register_all_methods(session)
    except Exception as exc:
        log.warning("orchestration.bootstrap.methods_registry_failed", error=str(exc))


_bootstrap_methods_registry()


# ----------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------
heartbeat_job = define_asset_job(
    name="heartbeat_job",
    selection=AssetSelection.assets(heartbeat_asset),
    description="Emit a heartbeat row to system.heartbeat.",
)

ingest_market_data_job = define_asset_job(
    name="ingest_market_data_job",
    selection=AssetSelection.assets(ingest_yfinance_bars),
    description="Daily ETF daily-bar ingest.",
)

ingest_macro_data_job = define_asset_job(
    name="ingest_macro_data_job",
    selection=AssetSelection.assets(ingest_fred_series),
    description="Daily FRED + ALFRED ingest (vintaged).",
)

ingest_positioning_job = define_asset_job(
    name="ingest_positioning_job",
    selection=AssetSelection.assets(ingest_cftc_cot),
    description="Weekly CFTC COT ingest.",
)

ingest_alt_data_job = define_asset_job(
    name="ingest_alt_data_job",
    selection=AssetSelection.assets(
        ingest_eia, ingest_usda, ingest_noaa_weather, ingest_google_trends
    ),
    description="Alt-data ingests (EIA / USDA / NOAA / Google Trends).",
)

ingest_calendar_job = define_asset_job(
    name="ingest_calendar_job",
    selection=AssetSelection.assets(refresh_calendar_events),
    description="Refresh calendar events from schedules.",
)

ingest_all_job = define_asset_job(
    name="ingest_all_job",
    selection=AssetSelection.assets(
        ingest_yfinance_bars,
        ingest_fred_series,
        ingest_cftc_cot,
        ingest_eia,
        ingest_usda,
        ingest_noaa_weather,
        ingest_google_trends,
        refresh_calendar_events,
    ),
    description="One-shot job to run every ingester (used for manual backfills).",
)

data_quality_job = define_asset_job(
    name="data_quality_job",
    selection=AssetSelection.assets(daily_data_quality),
    description="Daily quality run + comparator persistence.",
)

compute_all_signals_job = define_asset_job(
    name="compute_all_signals_job",
    selection=AssetSelection.assets(
        signal_trend,
        signal_carry,
        signal_value,
        signal_positioning,
        signal_dislocation,
        signal_factor_exposure,
        signal_catalyst,
        signal_alt_data,
        signal_nowcasting,
        signal_vol_surface,
    ),
    description=(
        "Daily computation of all 10 signal families."
    ),
)

dislocation_refit_job = define_asset_job(
    name="dislocation_refit_job",
    selection=AssetSelection.assets(dislocation_models_refit),
    description="Weekly refit of PCA + DFM dislocation models (Sunday 00:00 UTC).",
)

factor_exposure_refit_job = define_asset_job(
    name="factor_exposure_refit_job",
    selection=AssetSelection.assets(factor_exposure_models_refit),
    description=(
        "Weekly refit of OLS + RF + (optional) Causal Forest factor "
        "exposure models (Sunday 01:00 UTC)."
    ),
)

catalyst_refit_job = define_asset_job(
    name="catalyst_refit_job",
    selection=AssetSelection.assets(catalyst_models_refit),
    description=(
        "Weekly refit of catalyst sensitivity models (Sunday 02:00 UTC)."
    ),
)

nowcasting_refit_job = define_asset_job(
    name="nowcasting_refit_job",
    selection=AssetSelection.assets(nowcasting_models_refit),
    description=(
        "Weekly refit of OLS-AR + BVAR nowcasting models (Sunday 03:00 UTC)."
    ),
)


# ----------------------------------------------------------------------
# Schedules (all UTC)
# ----------------------------------------------------------------------
SCHEDULES = [
    ScheduleDefinition(
        name="heartbeat_every_5_minutes",
        cron_schedule="*/5 * * * *",
        job=heartbeat_job,
        execution_timezone="UTC",
        description="Heartbeat every 5 minutes (dev only).",
    ),
    ScheduleDefinition(
        name="ingest_market_data_daily_22_utc",
        cron_schedule="0 22 * * *",
        job=ingest_market_data_job,
        execution_timezone="UTC",
    ),
    ScheduleDefinition(
        name="ingest_macro_data_daily_13_utc",
        cron_schedule="0 13 * * *",
        job=ingest_macro_data_job,
        execution_timezone="UTC",
    ),
    ScheduleDefinition(
        name="ingest_positioning_weekly_friday_1900_utc",
        cron_schedule="0 19 * * 5",
        job=ingest_positioning_job,
        execution_timezone="UTC",
    ),
    ScheduleDefinition(
        name="ingest_alt_data_daily_15_utc",
        cron_schedule="0 15 * * *",
        job=ingest_alt_data_job,
        execution_timezone="UTC",
    ),
    ScheduleDefinition(
        name="ingest_calendar_weekly_sunday_06_utc",
        cron_schedule="0 6 * * 0",
        job=ingest_calendar_job,
        execution_timezone="UTC",
    ),
    ScheduleDefinition(
        name="data_quality_daily_23_utc",
        cron_schedule="0 23 * * *",
        job=data_quality_job,
        execution_timezone="UTC",
    ),
    ScheduleDefinition(
        name="compute_all_signals_daily_2330_utc",
        cron_schedule="30 23 * * *",
        job=compute_all_signals_job,
        execution_timezone="UTC",
        description="Daily trend + carry + value + positioning + dislocation signals.",
    ),
    ScheduleDefinition(
        name="dislocation_refit_weekly_sunday_0000_utc",
        cron_schedule="0 0 * * 0",
        job=dislocation_refit_job,
        execution_timezone="UTC",
        description="Weekly refit of PCA + DFM dislocation models.",
    ),
    ScheduleDefinition(
        name="factor_exposure_refit_weekly_sunday_0100_utc",
        cron_schedule="0 1 * * 0",
        job=factor_exposure_refit_job,
        execution_timezone="UTC",
        description="Weekly refit of OLS + RF + CF factor exposure models.",
    ),
    ScheduleDefinition(
        name="catalyst_refit_weekly_sunday_0200_utc",
        cron_schedule="0 2 * * 0",
        job=catalyst_refit_job,
        execution_timezone="UTC",
        description="Weekly refit of catalyst sensitivity models.",
    ),
    ScheduleDefinition(
        name="nowcasting_refit_weekly_sunday_0300_utc",
        cron_schedule="0 3 * * 0",
        job=nowcasting_refit_job,
        execution_timezone="UTC",
        description="Weekly refit of OLS-AR + BVAR nowcasting models.",
    ),
]


def _resources() -> dict[str, object]:
    return {
        "settings": get_settings(),
        "methods_registry": get_default_registry(),
    }


defs = Definitions(
    assets=ALL_ASSETS,
    jobs=[
        heartbeat_job,
        ingest_market_data_job,
        ingest_macro_data_job,
        ingest_positioning_job,
        ingest_alt_data_job,
        ingest_calendar_job,
        ingest_all_job,
        data_quality_job,
        compute_all_signals_job,
        dislocation_refit_job,
        factor_exposure_refit_job,
        catalyst_refit_job,
        nowcasting_refit_job,
    ],
    schedules=SCHEDULES,
    resources=_resources(),
)
