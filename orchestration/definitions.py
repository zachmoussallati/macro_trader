"""Dagster definitions root.

Defines the assets, schedules, and resources Dagster discovers when launched
via ``dagster dev -f orchestration/definitions.py``.

On code-location startup, this module calls
:func:`macro_trader.methods.setup.register_all_methods` so the methods
registry is rebuilt from code every time Dagster boots. This is idempotent
and keeps the registry self-healing across DB recreations.
"""

from __future__ import annotations

from dagster import AssetSelection, Definitions, ScheduleDefinition, define_asset_job

from macro_trader.config import get_settings
from macro_trader.db.engine import get_session
from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import get_default_registry
from macro_trader.methods.setup import register_all_methods
from orchestration.assets import heartbeat_asset

log = get_logger(__name__)


def _bootstrap_methods_registry() -> None:
    """Idempotent method registration on code-location startup.

    Failures are logged but do NOT prevent Dagster from loading the location
    — a transient DB blip shouldn't make every asset undeployable. The next
    boot will retry.
    """
    try:
        with get_session() as session:
            register_all_methods(session)
    except Exception as exc:
        log.warning("orchestration.bootstrap.methods_registry_failed", error=str(exc))


_bootstrap_methods_registry()


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
