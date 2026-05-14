"""SQLAlchemy ORM models.

Importing this module registers all model classes with the
``Base.metadata`` registry, which Alembic relies on for autogenerate.
"""

from macro_trader.db.models.alt_data import (
    EIAInventory,
    GoogleTrends,
    USDAReport,
    WeatherData,
)
from macro_trader.db.models.auth import RefreshTokenRow, User, UserRole
from macro_trader.db.models.macro_data import CalendarEvent, Series, SeriesObservation
from macro_trader.db.models.market_data import DailyBar, Instrument
from macro_trader.db.models.positioning import COTWeekly
from macro_trader.db.models.system import (
    DataFreshness,
    DataLineage,
    DataQualityFlag,
    DataSource,
    HeartbeatRow,
    MethodComparisonRow,
    MethodRegistryRow,
    MethodStatusHistoryRow,
)

__all__ = [
    "COTWeekly",
    "CalendarEvent",
    "DailyBar",
    "DataFreshness",
    "DataLineage",
    "DataQualityFlag",
    "DataSource",
    "EIAInventory",
    "GoogleTrends",
    "HeartbeatRow",
    "Instrument",
    "MethodComparisonRow",
    "MethodRegistryRow",
    "MethodStatusHistoryRow",
    "RefreshTokenRow",
    "Series",
    "SeriesObservation",
    "USDAReport",
    "User",
    "UserRole",
    "WeatherData",
]
