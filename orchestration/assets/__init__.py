"""Dagster assets."""

from orchestration.assets.calendar import CALENDAR_ASSETS, refresh_calendar_events
from orchestration.assets.data_quality import DATA_QUALITY_ASSETS, daily_data_quality
from orchestration.assets.heartbeat import heartbeat_asset
from orchestration.assets.ingest import (
    INGEST_ASSETS,
    ingest_cftc_cot,
    ingest_eia,
    ingest_fred_series,
    ingest_google_trends,
    ingest_noaa_weather,
    ingest_usda,
    ingest_yfinance_bars,
)
from orchestration.assets.signals import (
    SIGNAL_ASSETS,
    signal_carry,
    signal_positioning,
    signal_trend,
    signal_value,
)

ALL_ASSETS = [
    heartbeat_asset,
    *INGEST_ASSETS,
    *CALENDAR_ASSETS,
    *DATA_QUALITY_ASSETS,
    *SIGNAL_ASSETS,
]

__all__ = [
    "ALL_ASSETS",
    "CALENDAR_ASSETS",
    "DATA_QUALITY_ASSETS",
    "INGEST_ASSETS",
    "SIGNAL_ASSETS",
    "daily_data_quality",
    "heartbeat_asset",
    "ingest_cftc_cot",
    "ingest_eia",
    "ingest_fred_series",
    "ingest_google_trends",
    "ingest_noaa_weather",
    "ingest_usda",
    "ingest_yfinance_bars",
    "refresh_calendar_events",
    "signal_carry",
    "signal_positioning",
    "signal_trend",
    "signal_value",
]
