"""Domain schemas.

Each pipeline layer owns a Postgres schema; tables are scoped to their layer
so cross-layer joins are explicit. Alembic migrations are created with
``version_table_schema`` matching each domain, but for Stage 1 we use a single
``alembic_version`` table in ``system``.
"""

from __future__ import annotations

# Order matters for creation/teardown: `system` first (heartbeat, methods,
# alembic version), `auth` second (depends on nothing), then the rest.
DOMAIN_SCHEMAS: tuple[str, ...] = (
    "system",
    "auth",
    "market_data",
    "positioning",
    "macro_data",
    "alt_data",
    "signals",
    "regime",
    "portfolio",
    "backtest",
    "claude_layer",
)
