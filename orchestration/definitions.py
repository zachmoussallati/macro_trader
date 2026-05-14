"""Dagster definitions root.

Defines the assets, schedules, and resources Dagster discovers when launched
via ``dagster dev -f orchestration/definitions.py``.
"""

from __future__ import annotations

from dagster import AssetSelection, Definitions, ScheduleDefinition, define_asset_job

from macro_trader.config import get_settings
from macro_trader.methods.registry import get_default_registry
from orchestration.assets import heartbeat_asset

heartbeat_job = define_asset_job(
    name="heartbeat_job",
    selection=AssetSelection.assets(heartbeat_asset),
    description="Emit a heartbeat row to system.heartbeat.",
)

heartbeat_schedule = ScheduleDefinition(
    name="heartbeat_every_5_minutes",
    cron_schedule="*/5 * * * *",
    job=heartbeat_job,
    execution_timezone="UTC",
    description="Heartbeat every 5 minutes (dev only).",
)


def _resources() -> dict[str, object]:
    # Stage 1: keep resources simple. Later stages will register a Postgres IO
    # manager, a methods-registry resource, etc.
    return {
        "settings": get_settings(),
        "methods_registry": get_default_registry(),
    }


defs = Definitions(
    assets=[heartbeat_asset],
    jobs=[heartbeat_job],
    schedules=[heartbeat_schedule],
    resources=_resources(),
)
