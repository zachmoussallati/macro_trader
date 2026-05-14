"""Heartbeat asset.

Proves the Dagster orchestrator + DB connection are both live. Writes one
row to ``system.heartbeat`` each tick.
"""

from __future__ import annotations

from typing import Any

from dagster import MaterializeResult, MetadataValue, asset

from macro_trader.db.engine import get_session
from macro_trader.db.models.system import HeartbeatRow
from macro_trader.utils.dates import utcnow


@asset(
    group_name="system",
    description="Pipeline liveness pulse. Writes one row to system.heartbeat per tick.",
)
def heartbeat_asset(context) -> MaterializeResult:
    now = utcnow()
    meta: dict[str, Any] = {
        "run_id": context.run_id,
        "asset_key": "heartbeat_asset",
    }
    with get_session() as session:
        session.add(HeartbeatRow(timestamp=now, source="dagster.heartbeat_asset", meta=meta))
    return MaterializeResult(
        metadata={
            "timestamp": MetadataValue.text(now.isoformat()),
            "source": MetadataValue.text("dagster.heartbeat_asset"),
        }
    )
