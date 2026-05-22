"""Per-source ingest assets.

One asset per source. Each constructs the ingester with the global
sessionmaker + settings and calls ``run()``. Errors propagate to Dagster so
asset health is visible in the UI.

Calendar refresh and the daily data-quality job are separate assets in
``orchestration/assets/data_quality.py`` and ``calendar.py``.
"""

# NOTE: do NOT add `from __future__ import annotations` here. Dagster's
# `_validate_context_type_hint` resolves `context: AssetExecutionContext`
# at decorator-time and rejects string-form annotations.

from datetime import UTC

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


# ----------------------------------------------------------------------
# Vol surface — Stage 5 options-chain ingest
# ----------------------------------------------------------------------
@asset(
    group_name="ingest_market_data",
    description=(
        "yfinance options-chain snapshots for the vol_surface universe "
        "(GLD/SLV/USO/UNG/DBA/SPY). Source-agnostic: swap "
        "YfinanceOptionsIngester for a paid-source ingester via the "
        "OptionsChainIngester ABC and this asset reuses unchanged. "
        "Idempotent UPSERT via on_conflict_do_nothing on the natural PK."
    ),
    auto_materialize_policy=AutoMaterializePolicy.eager(),
)
def ingest_options_chains(context: AssetExecutionContext) -> MaterializeResult:
    from datetime import datetime

    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from macro_trader.db.models.market_data import Instrument, OptionsChain
    from macro_trader.signals.vol_surface.ingestion.yfinance import (
        YfinanceOptionsIngester,
    )
    from macro_trader.signals.vol_surface.methods import VOL_SURFACE_UNIVERSE

    ingester = YfinanceOptionsIngester()
    session_factory = get_sessionmaker()
    now = datetime.now(UTC)
    rows_per_instrument: dict[str, int] = {}
    rows_total = 0
    with session_factory() as session:
        instruments = list(
            session.scalars(
                select(Instrument)
                .where(Instrument.is_active.is_(True))
                .where(Instrument.proxy_ticker.is_not(None))
            )
        )
        for inst in instruments:
            if inst.proxy_ticker not in VOL_SURFACE_UNIVERSE:
                continue
            try:
                chain_rows = ingester.fetch_chain(
                    inst.instrument_id,
                    proxy_ticker=inst.proxy_ticker,
                    now=now,
                )
            except Exception as exc:  # pragma: no cover - yfinance flake
                context.log.warning(
                    f"vol_surface.ingest_chain.failed instrument={inst.instrument_id}"
                    f" ticker={inst.proxy_ticker} error={exc}"
                )
                continue
            if not chain_rows:
                continue
            payload = [
                {
                    "instrument_id": r.instrument_id,
                    "snapshot_ts": r.snapshot_ts,
                    "expiry_ts": r.expiry_ts,
                    "strike": r.strike,
                    "option_type": r.option_type,
                    "bid": r.bid,
                    "ask": r.ask,
                    "last": r.last,
                    "volume": r.volume,
                    "open_interest": r.open_interest,
                    "implied_vol": r.implied_vol,
                    "delta": r.delta,
                    "gamma": r.gamma,
                    "vega": r.vega,
                    "theta": r.theta,
                    "underlying_price": r.underlying_price,
                    "source": r.source,
                }
                for r in chain_rows
            ]
            stmt = pg_insert(OptionsChain).values(payload)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[
                    "instrument_id",
                    "snapshot_ts",
                    "expiry_ts",
                    "strike",
                    "option_type",
                ]
            )
            result = session.execute(stmt)
            inserted = int(result.rowcount or 0)
            rows_per_instrument[inst.instrument_id] = inserted
            rows_total += inserted
        session.commit()

    context.log.info(f"vol_surface.ingest_chains rows={rows_per_instrument}")
    return MaterializeResult(
        metadata={
            "rows_ingested": MetadataValue.int(rows_total),
            "per_instrument": MetadataValue.json(rows_per_instrument),
            "source_id": MetadataValue.text(ingester.source),
        }
    )


INGEST_ASSETS = [
    ingest_yfinance_bars,
    ingest_fred_series,
    ingest_cftc_cot,
    ingest_eia,
    ingest_usda,
    ingest_noaa_weather,
    ingest_google_trends,
    ingest_options_chains,
]
