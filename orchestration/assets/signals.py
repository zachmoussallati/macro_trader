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


SIGNAL_ASSETS = [signal_trend, signal_carry, signal_value, signal_positioning]
