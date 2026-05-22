"""Daily data-quality asset.

Runs after the market-data ingest completes, picks up the latest close
series per instrument, runs all registered data-quality methods, persists
flags, and runs the comparator.
"""

# NOTE: do NOT add `from __future__ import annotations` here. Dagster's
# `_validate_context_type_hint` resolves `context: AssetExecutionContext`
# at decorator-time and rejects string-form annotations.

from dagster import (
    AssetExecutionContext,
    AssetIn,
    MaterializeResult,
    MetadataValue,
    asset,
)

from macro_trader.data.quality.runner import run_daily_quality_check
from macro_trader.db.engine import get_sessionmaker
from macro_trader.methods.registry import get_default_registry
from macro_trader.methods.setup import register_all_methods


@asset(
    group_name="data_quality",
    description="Daily data-quality run: every registered method + comparator.",
    ins={"ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars")},
)
def daily_data_quality(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    # Ensure methods are registered (idempotent — code-location startup
    # already ran this, but make the asset robust to fresh DBs).
    registry = get_default_registry()
    with session_factory() as session:
        if not registry.list_methods(component="data_quality"):
            register_all_methods(session)
            session.commit()
        summary = run_daily_quality_check(session)
    total_flags = sum(sum(v.values()) for v in summary.values())
    context.log.info(f"data_quality.daily.total_flags={total_flags}")
    return MaterializeResult(
        metadata={
            "instruments_processed": MetadataValue.int(len(summary)),
            "total_flags": MetadataValue.int(total_flags),
        }
    )


DATA_QUALITY_ASSETS = [daily_data_quality]
