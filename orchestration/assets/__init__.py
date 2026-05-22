"""Dagster assets."""

from orchestration.assets.calendar import CALENDAR_ASSETS, refresh_calendar_events
from orchestration.assets.composite import (
    COMPOSITE_ASSETS,
    composite_bayesian_refit,
    composite_gbm_refit,
    composite_score_compute,
    composite_weights_refit,
)
from orchestration.assets.data_quality import DATA_QUALITY_ASSETS, daily_data_quality
from orchestration.assets.heartbeat import heartbeat_asset
from orchestration.assets.ingest import (
    INGEST_ASSETS,
    ingest_cftc_cot,
    ingest_eia,
    ingest_fred_series,
    ingest_google_trends,
    ingest_noaa_weather,
    ingest_options_chains,
    ingest_usda,
    ingest_yfinance_bars,
)
from orchestration.assets.portfolio import (
    PORTFOLIO_ASSETS,
    covariance_dcc_refit,
    covariance_estimates_daily,
    portfolio_positions_daily,
)
from orchestration.assets.regime import (
    REGIME_ASSETS,
    regime_attribution_compute,
    regime_classification,
    regime_models_refit,
)
from orchestration.assets.signals import (
    SIGNAL_ASSETS,
    catalyst_models_refit,
    dislocation_models_refit,
    factor_exposure_models_refit,
    nowcasting_models_refit,
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

ALL_ASSETS = [
    heartbeat_asset,
    *INGEST_ASSETS,
    *CALENDAR_ASSETS,
    *DATA_QUALITY_ASSETS,
    *SIGNAL_ASSETS,
    *REGIME_ASSETS,
    *COMPOSITE_ASSETS,
    *PORTFOLIO_ASSETS,
]

__all__ = [
    "ALL_ASSETS",
    "CALENDAR_ASSETS",
    "COMPOSITE_ASSETS",
    "DATA_QUALITY_ASSETS",
    "INGEST_ASSETS",
    "PORTFOLIO_ASSETS",
    "REGIME_ASSETS",
    "SIGNAL_ASSETS",
    "catalyst_models_refit",
    "composite_bayesian_refit",
    "composite_gbm_refit",
    "composite_score_compute",
    "composite_weights_refit",
    "covariance_dcc_refit",
    "covariance_estimates_daily",
    "daily_data_quality",
    "dislocation_models_refit",
    "factor_exposure_models_refit",
    "heartbeat_asset",
    "ingest_cftc_cot",
    "ingest_eia",
    "ingest_fred_series",
    "ingest_google_trends",
    "ingest_noaa_weather",
    "ingest_options_chains",
    "ingest_usda",
    "ingest_yfinance_bars",
    "nowcasting_models_refit",
    "portfolio_positions_daily",
    "refresh_calendar_events",
    "regime_attribution_compute",
    "regime_classification",
    "regime_models_refit",
    "signal_alt_data",
    "signal_carry",
    "signal_catalyst",
    "signal_dislocation",
    "signal_factor_exposure",
    "signal_nowcasting",
    "signal_positioning",
    "signal_trend",
    "signal_value",
    "signal_vol_surface",
]
