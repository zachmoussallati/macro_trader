"""Calendar event ingestion (release schedules + manual)."""

from macro_trader.calendar.ingestion.eia_schedule import seed_eia_schedule
from macro_trader.calendar.ingestion.fomc_schedule import seed_fomc_schedule
from macro_trader.calendar.ingestion.fred_releases import seed_fred_releases
from macro_trader.calendar.ingestion.manual import seed_manual_events
from macro_trader.calendar.ingestion.usda_schedule import seed_usda_schedule

__all__ = [
    "seed_eia_schedule",
    "seed_fomc_schedule",
    "seed_fred_releases",
    "seed_manual_events",
    "seed_usda_schedule",
]
