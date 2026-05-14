"""SQLAlchemy ORM models.

Importing this module registers all model classes with the
``Base.metadata`` registry, which Alembic relies on for autogenerate.
"""

from macro_trader.db.models.auth import RefreshTokenRow, User, UserRole
from macro_trader.db.models.system import (
    HeartbeatRow,
    MethodComparisonRow,
    MethodRegistryRow,
    MethodStatusHistoryRow,
)

__all__ = [
    "HeartbeatRow",
    "MethodComparisonRow",
    "MethodRegistryRow",
    "MethodStatusHistoryRow",
    "RefreshTokenRow",
    "User",
    "UserRole",
]
