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
    composite_bayesian_refit,
    composite_gbm_refit,
    composite_score_compute,
    composite_weights_refit,
    covariance_dcc_refit,
    covariance_estimates_daily,
    daily_data_quality,
    dislocation_models_refit,
    factor_exposure_models_refit,
    heartbeat_asset,
    ingest_cftc_cot,
    ingest_eia,
    ingest_fred_series,
    ingest_google_trends,
    ingest_noaa_weather,
    ingest_options_chains,
    ingest_usda,
    ingest_yfinance_bars,
    nowcasting_models_refit,
    portfolio_positions_daily,
    refresh_calendar_events,
    regime_attribution_compute,
    regime_classification,
    regime_models_refit,
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

ingest_options_chains_job = define_asset_job(
    name="ingest_options_chains_job",
    selection=AssetSelection.assets(ingest_options_chains),
    description=(
        "Daily yfinance options-chain snapshots for the vol_surface "
        "universe (21:30 UTC, after US equity options close at 20:00 "
        "UTC post-DST)."
    ),
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
        ingest_options_chains,
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

regime_refit_job = define_asset_job(
    name="regime_refit_job",
    selection=AssetSelection.assets(regime_models_refit),
    description=(
        "Weekly refit of GMM + (quarterly) HMM + MS-VAR regime "
        "classifiers (Sunday 04:00 UTC)."
    ),
)

regime_classification_job = define_asset_job(
    name="regime_classification_job",
    selection=AssetSelection.assets(regime_classification),
    description="Daily regime classification across all 5 methods.",
)

regime_attribution_job = define_asset_job(
    name="regime_attribution_job",
    selection=AssetSelection.assets(regime_attribution_compute),
    description=(
        "Weekly per-regime per-signal attribution compute (Sunday "
        "05:00 UTC). Populates regime.regime_attribution for Stage 7 "
        "composite scoring."
    ),
)

composite_weights_refit_job = define_asset_job(
    name="composite_weights_refit_job",
    selection=AssetSelection.assets(composite_weights_refit),
    description=(
        "Weekly refit of the composite weight snapshot (Sunday 06:00 "
        "UTC, after attribution at 05:00). Reads regime.regime_"
        "attribution + writes signals.composite_weights."
    ),
)

composite_bayesian_refit_job = define_asset_job(
    name="composite_bayesian_refit_job",
    selection=AssetSelection.assets(composite_bayesian_refit),
    description=(
        "Weekly fit of the Bayesian hierarchical composite (Sunday "
        "06:30 UTC, after composite weights at 06:00)."
    ),
)

composite_gbm_refit_job = define_asset_job(
    name="composite_gbm_refit_job",
    selection=AssetSelection.assets(composite_gbm_refit),
    description=(
        "Quarterly fit of the LightGBM composite (first Sunday of "
        "Jan/Apr/Jul/Oct at 07:00 UTC)."
    ),
)

composite_score_job = define_asset_job(
    name="composite_score_job",
    selection=AssetSelection.assets(composite_score_compute),
    description=(
        "Daily composite scoring across all 3 methods (23:45 UTC; 15 "
        "min after regime + signals at 23:30)."
    ),
)

covariance_estimates_job = define_asset_job(
    name="covariance_estimates_job",
    selection=AssetSelection.assets(covariance_estimates_daily),
    description=(
        "Daily covariance estimates (23:50 UTC; 5 min after composite "
        "scoring at 23:45)."
    ),
)

covariance_dcc_refit_job = define_asset_job(
    name="covariance_dcc_refit_job",
    selection=AssetSelection.assets(covariance_dcc_refit),
    description="Weekly DCC-GARCH refit (Sunday 07:30 UTC).",
)

portfolio_positions_job = define_asset_job(
    name="portfolio_positions_job",
    selection=AssetSelection.assets(portfolio_positions_daily),
    description=(
        "Daily portfolio sizing (next-day 00:05 UTC). Runs every "
        "portfolio method; applies the production drawdown gate."
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
        name="ingest_options_chains_daily_2130_utc",
        cron_schedule="30 21 * * *",
        job=ingest_options_chains_job,
        execution_timezone="UTC",
        description=(
            "Daily yfinance options-chain snapshots (after US equity "
            "options close at 20:00 UTC post-DST)."
        ),
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
    ScheduleDefinition(
        name="regime_refit_weekly_sunday_0400_utc",
        cron_schedule="0 4 * * 0",
        job=regime_refit_job,
        execution_timezone="UTC",
        description="Weekly refit of regime classifiers (GMM weekly; HMM/MS-VAR quarterly).",
    ),
    ScheduleDefinition(
        name="regime_attribution_weekly_sunday_0500_utc",
        cron_schedule="0 5 * * 0",
        job=regime_attribution_job,
        execution_timezone="UTC",
        description="Weekly per-regime per-signal performance attribution.",
    ),
    ScheduleDefinition(
        name="regime_classification_daily_2330_utc",
        cron_schedule="30 23 * * *",
        job=regime_classification_job,
        execution_timezone="UTC",
        description="Daily regime classification (alongside compute_all_signals_job).",
    ),
    ScheduleDefinition(
        name="composite_weights_refit_weekly_sunday_0600_utc",
        cron_schedule="0 6 * * 0",
        job=composite_weights_refit_job,
        execution_timezone="UTC",
        description=(
            "Weekly refresh of the composite weight snapshot from "
            "the regime attribution table."
        ),
    ),
    ScheduleDefinition(
        name="composite_bayesian_refit_weekly_sunday_0630_utc",
        cron_schedule="30 6 * * 0",
        job=composite_bayesian_refit_job,
        execution_timezone="UTC",
        description="Weekly Bayesian hierarchical composite fit.",
    ),
    ScheduleDefinition(
        name="composite_gbm_refit_quarterly_first_sunday_0700_utc",
        # First Sunday of Jan/Apr/Jul/Oct at 07:00. cron doesn't
        # directly express "first Sunday of month"; we use day-of-month
        # in [1-7] AND day-of-week=0 (Sunday).
        cron_schedule="0 7 1-7 1,4,7,10 0",
        job=composite_gbm_refit_job,
        execution_timezone="UTC",
        description="Quarterly LightGBM composite fit.",
    ),
    ScheduleDefinition(
        name="composite_score_daily_2345_utc",
        cron_schedule="45 23 * * *",
        job=composite_score_job,
        execution_timezone="UTC",
        description=(
            "Daily composite scoring across all 3 methods (15 min "
            "after regime + signals at 23:30)."
        ),
    ),
    ScheduleDefinition(
        name="covariance_estimates_daily_2350_utc",
        cron_schedule="50 23 * * *",
        job=covariance_estimates_job,
        execution_timezone="UTC",
        description=(
            "Daily covariance estimates (5 min after composite scoring "
            "at 23:45)."
        ),
    ),
    ScheduleDefinition(
        name="portfolio_positions_daily_0005_utc",
        cron_schedule="5 0 * * *",
        job=portfolio_positions_job,
        execution_timezone="UTC",
        description=(
            "Daily portfolio sizing (15 min after covariance + composite "
            "at 23:45/23:50 the prior day)."
        ),
    ),
    ScheduleDefinition(
        name="covariance_dcc_refit_weekly_sunday_0730_utc",
        cron_schedule="30 7 * * 0",
        job=covariance_dcc_refit_job,
        execution_timezone="UTC",
        description="Weekly DCC-GARCH refit.",
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
        ingest_options_chains_job,
        data_quality_job,
        compute_all_signals_job,
        dislocation_refit_job,
        factor_exposure_refit_job,
        catalyst_refit_job,
        nowcasting_refit_job,
        regime_refit_job,
        regime_classification_job,
        regime_attribution_job,
        composite_weights_refit_job,
        composite_bayesian_refit_job,
        composite_gbm_refit_job,
        composite_score_job,
        covariance_estimates_job,
        covariance_dcc_refit_job,
        portfolio_positions_job,
    ],
    schedules=SCHEDULES,
    resources=_resources(),
)
