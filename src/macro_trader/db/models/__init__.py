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
from macro_trader.db.models.market_data import (
    DailyBar,
    Instrument,
    OptionsChain,
    OptionsSurface,
)
from macro_trader.db.models.portfolio import (
    CovarianceEstimate,
    DrawdownStateRow,
    EquityCurveRow,
    Position,
    VolatilityEstimate,
)
from macro_trader.db.models.positioning import COTWeekly
from macro_trader.db.models.regime import RegimeAttribution, RegimeState
from macro_trader.db.models.signals import CompositeScore, CompositeWeight, SignalValue
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
    "CompositeScore",
    "CompositeWeight",
    "CovarianceEstimate",
    "DailyBar",
    "DataFreshness",
    "DataLineage",
    "DataQualityFlag",
    "DataSource",
    "DrawdownStateRow",
    "EIAInventory",
    "EquityCurveRow",
    "GoogleTrends",
    "HeartbeatRow",
    "Instrument",
    "MethodComparisonRow",
    "MethodRegistryRow",
    "MethodStatusHistoryRow",
    "OptionsChain",
    "OptionsSurface",
    "Position",
    "RefreshTokenRow",
    "RegimeAttribution",
    "RegimeState",
    "Series",
    "SeriesObservation",
    "SignalValue",
    "USDAReport",
    "User",
    "UserRole",
    "VolatilityEstimate",
    "WeatherData",
]
