"""Calendar refresh asset (release schedules + FOMC + manual events)."""

from __future__ import annotations

from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from macro_trader.calendar.ingestion import (
    seed_eia_schedule,
    seed_fomc_schedule,
    seed_fred_releases,
    seed_manual_events,
    seed_usda_schedule,
)
from macro_trader.calendar.ingestion.usda_schedule import seed_drought_monitor_schedule
from macro_trader.config import get_settings
from macro_trader.db.engine import get_sessionmaker


@asset(
    group_name="ingest_calendar",
    description="Refresh calendar events: FRED releases + EIA + USDA + FOMC + manual.",
)
def refresh_calendar_events(context: AssetExecutionContext) -> MaterializeResult:
    settings = get_settings()
    session_factory = get_sessionmaker()
    counts: dict[str, int] = {}
    with session_factory() as session:
        counts["fred_releases"] = seed_fred_releases(
            session, api_key=settings.data_sources.fred_api_key
        )
        counts["eia_schedule"] = seed_eia_schedule(session)
        counts["usda_schedule"] = seed_usda_schedule(session)
        counts["drought_monitor"] = seed_drought_monitor_schedule(session)
        counts["fomc_schedule"] = seed_fomc_schedule(session)
        counts["manual"] = seed_manual_events(session)
    context.log.info(f"calendar.refresh.counts={counts}")
    return MaterializeResult(metadata={k: MetadataValue.int(v) for k, v in counts.items()})


CALENDAR_ASSETS = [refresh_calendar_events]
