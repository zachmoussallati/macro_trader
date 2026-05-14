"""Central methods registration entrypoint.

Stages 2+ register their methods through this module so the registry is
self-rebuilding from code on every Dagster code-location start. Each
pipeline component owns its own ``register.py`` module that exposes a
single ``register(session)`` function; this module imports them all and
calls each in turn.

``register_all_methods`` is **idempotent** — running it twice produces no
new rows in ``system.methods_registry`` (the registry checks for existing
method_ids before inserting).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.logging_setup import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def register_all_methods(session: Session) -> None:
    """Register every project method into the registry.

    Add new components below as they come online (Stage 2: data quality;
    Stage 3+: signal / regime / portfolio components).
    """
    # ----- Stage 2 -----
    try:
        from macro_trader.data.quality.register import register as register_data_quality

        register_data_quality(session)
    except ImportError:
        # data quality module not yet present (running pre-Stage-2 code).
        log.info("methods.setup.skipped", component="data_quality", reason="module not present")

    # ----- Stage 3+ — add registrations here. -----

    log.info("methods.setup.complete")
