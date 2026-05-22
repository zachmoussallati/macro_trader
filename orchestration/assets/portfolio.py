"""Portfolio Dagster assets (Stage 8).

Five assets:

- ``covariance_estimates_daily`` — daily 23:50 UTC (5 min after the
  composite). Pulls returns; runs Ledoit-Wolf + DCC-GARCH; persists.
- ``covariance_dcc_refit`` — weekly Sunday 07:30 UTC. Re-fits the
  DCC-GARCH parameters on the trailing 504-day panel.
- ``portfolio_positions_daily`` — daily 00:05 UTC the next day.
  Reads composite + covariance + drawdown gate; runs every portfolio
  method; persists positions.
- ``drawdown_state_update`` — daily 00:10 UTC the next day. Realises
  yesterday's positions * today's returns, updates the equity curve,
  evaluates the gate. Wired before positions so positions can read
  the new gate state.

  (Actually the runner calls update_drawdown_state internally so the
  gate-update + positions happen in one job; we still expose a
  standalone refresh asset for manual operator triggers.)
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
from macro_trader.portfolio.construction.runner import run_daily_portfolio
from macro_trader.portfolio.covariance.refit import refit_dcc_garch
from macro_trader.portfolio.covariance.runner import run_daily_covariance


@asset(
    group_name="portfolio",
    description=(
        "Daily covariance estimates (23:50 UTC, 5 min after composite "
        "scoring at 23:45). Runs Ledoit-Wolf baseline + DCC-GARCH "
        "shadow. Persists one row per (method, as_of, pair) to "
        "portfolio.covariance_estimates and per-instrument vol to "
        "portfolio.volatility_estimates."
    ),
    ins={
        "composite_score_compute": AssetIn(key="composite_score_compute"),
    },
)
def covariance_estimates_daily(
    context: AssetExecutionContext,
    composite_score_compute: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        written = run_daily_covariance(session)
        session.commit()
    context.log.info(f"portfolio.covariance.daily.written={written}")
    return MaterializeResult(
        metadata={
            "rows_written_total": MetadataValue.int(sum(written.values())),
            "per_method": MetadataValue.json(written),
        }
    )


@asset(
    group_name="portfolio",
    description=(
        "Weekly DCC-GARCH refit (Sunday 07:30 UTC). Re-estimates the "
        "per-instrument GARCH(1,1) + DCC correlation dynamics on the "
        "trailing 504-day return panel. Persists fitted state to "
        "system.methods_registry.serialized_blob."
    ),
    ins={
        "ingest_yfinance_bars": AssetIn(key="ingest_yfinance_bars"),
    },
)
def covariance_dcc_refit(
    context: AssetExecutionContext,
    ingest_yfinance_bars: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        result = refit_dcc_garch(session)
        session.commit()
    if result is None:
        context.log.info("portfolio.covariance.dcc.no_state")
        return MaterializeResult(metadata={"blob_size_bytes": MetadataValue.int(0)})
    return MaterializeResult(
        metadata={
            "blob_size_bytes": MetadataValue.int(result.blob_size_bytes),
            "fit_rows": MetadataValue.int(result.fit_rows),
            "compressed": MetadataValue.bool(result.compressed),
        }
    )


@asset(
    group_name="portfolio",
    description=(
        "Daily portfolio sizing (00:05 UTC, next day after composite + "
        "covariance). Runs every registered portfolio method; applies "
        "the production-method drawdown gate scaling; persists rows "
        "to portfolio.positions and runs the cross-method comparator."
    ),
    ins={
        "covariance_estimates_daily": AssetIn(key="covariance_estimates_daily"),
    },
)
def portfolio_positions_daily(
    context: AssetExecutionContext,
    covariance_estimates_daily: None,
) -> MaterializeResult:
    session_factory = get_sessionmaker()
    with session_factory() as session:
        written = run_daily_portfolio(session)
        session.commit()
    context.log.info(f"portfolio.positions.daily.written={written}")
    return MaterializeResult(
        metadata={
            "rows_written_total": MetadataValue.int(sum(written.values())),
            "per_method": MetadataValue.json(written),
        }
    )


PORTFOLIO_ASSETS = [
    covariance_estimates_daily,
    covariance_dcc_refit,
    portfolio_positions_daily,
]
