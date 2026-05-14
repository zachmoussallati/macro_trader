"""Per-source ingest assets.

One asset per source. Each constructs the ingester with the global
sessionmaker + settings and calls ``run()``. Errors propagate to Dagster so
asset health is visible in the UI.

Calendar refresh and the daily data-quality job are separate assets in
``orchestration/assets/data_quality.py`` and ``calendar.py``.
"""

from __future__ import annotations

from dagster import (
    AssetExecutionContext,
    AutoMaterializePolicy,
    MaterializeResult,
    MetadataValue,
    asset,
)

from macro_trader.config import get_settings
from macro_trader.data.ingestion.cftc import CFTCIngester
from macro_trader.data.ingestion.eia import EIAIngester
from macro_trader.data.ingestion.fred import FREDIngester
from macro_trader.data.ingestion.google_trends import GoogleTrendsIngester
from macro_trader.data.ingestion.noaa import NOAAIngester
from macro_trader.data.ingestion.usda import USDAIngester
from macro_trader.data.ingestion.yfinance_source import YFinanceIngester
from macro_trader.db.engine import get_sessionmaker


def _run_ingester(ingester_cls, context: AssetExecutionContext) -> MaterializeResult:
    """Common runner: construct + run, returning a MaterializeResult with stats."""
    settings = get_settings()
    session_factory = get_sessionmaker()
    ingester = ingester_cls(session_factory=session_factory, settings=settings)
    stats = ingester.run(
        dagster_run_id=context.run_id,
        dagster_asset_key=context.asset_key.to_user_string(),
    )
    return MaterializeResult(
        metadata={
            "rows_ingested": MetadataValue.int(stats.rows_ingested),
            "rows_updated": MetadataValue.int(stats.rows_updated),
            "rows_rejected": MetadataValue.int(stats.rows_rejected),
            "errors": MetadataValue.int(stats.error_count),
            "source_id": MetadataValue.text(ingester.source_id),
        }
    )


# ----------------------------------------------------------------------
# Market data
# ----------------------------------------------------------------------
@asset(
    group_name="ingest_market_data",
    description="ETF daily bars via yfinance (proxies for 13 commodity futures).",
    auto_materialize_policy=AutoMaterializePolicy.eager(),
)
def ingest_yfinance_bars(context: AssetExecutionContext) -> MaterializeResult:
    return _run_ingester(YFinanceIngester, context)


# ----------------------------------------------------------------------
# Macro data
# ----------------------------------------------------------------------
@asset(
    group_name="ingest_macro_data",
    description="FRED + ALFRED macro series; preserves every vintage.",
    auto_materialize_policy=AutoMaterializePolicy.eager(),
)
def ingest_fred_series(context: AssetExecutionContext) -> MaterializeResult:
    return _run_ingester(FREDIngester, context)


# ----------------------------------------------------------------------
# Positioning
# ----------------------------------------------------------------------
@asset(
    group_name="ingest_positioning",
    description="CFTC Commitments of Traders disaggregated futures report.",
    auto_materialize_policy=AutoMaterializePolicy.eager(),
)
def ingest_cftc_cot(context: AssetExecutionContext) -> MaterializeResult:
    return _run_ingester(CFTCIngester, context)


# ----------------------------------------------------------------------
# Alt data
# ----------------------------------------------------------------------
@asset(
    group_name="ingest_alt_data",
    description="EIA weekly petroleum + natural gas storage.",
    auto_materialize_policy=AutoMaterializePolicy.eager(),
)
def ingest_eia(context: AssetExecutionContext) -> MaterializeResult:
    return _run_ingester(EIAIngester, context)


@asset(
    group_name="ingest_alt_data",
    description="USDA NASS WASDE-style production / yield for major grains.",
    auto_materialize_policy=AutoMaterializePolicy.eager(),
)
def ingest_usda(context: AssetExecutionContext) -> MaterializeResult:
    return _run_ingester(USDAIngester, context)


@asset(
    group_name="ingest_alt_data",
    description="NOAA CDO HDD/CDD aggregates.",
    auto_materialize_policy=AutoMaterializePolicy.eager(),
)
def ingest_noaa_weather(context: AssetExecutionContext) -> MaterializeResult:
    return _run_ingester(NOAAIngester, context)


@asset(
    group_name="ingest_alt_data",
    description="Google Trends search-interest indices.",
    auto_materialize_policy=AutoMaterializePolicy.eager(),
)
def ingest_google_trends(context: AssetExecutionContext) -> MaterializeResult:
    return _run_ingester(GoogleTrendsIngester, context)


INGEST_ASSETS = [
    ingest_yfinance_bars,
    ingest_fred_series,
    ingest_cftc_cot,
    ingest_eia,
    ingest_usda,
    ingest_noaa_weather,
    ingest_google_trends,
]
