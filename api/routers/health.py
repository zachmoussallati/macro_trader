"""Health-check endpoint.

Reports:
- service liveness
- DB reachability + presence of `system.heartbeat`
- methods framework status (registry size + DB row count)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select, text

from api.deps import SessionDep
from macro_trader import __version__
from macro_trader.db.models.system import HeartbeatRow, MethodRegistryRow
from macro_trader.methods.registry import get_default_registry
from macro_trader.utils.dates import utcnow

router = APIRouter(tags=["health"])


@router.get("/health")
def health(session: SessionDep) -> dict[str, Any]:
    status_obj: dict[str, Any] = {
        "service": "macro-trader",
        "version": __version__,
        "now": utcnow().isoformat(),
        "checks": {},
    }

    # ----- DB connectivity. -----
    try:
        session.execute(text("SELECT 1"))
        status_obj["checks"]["database"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        status_obj["checks"]["database"] = {"ok": False, "error": str(exc)}

    # ----- TimescaleDB presence. -----
    try:
        ts = session.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")).scalar()
        status_obj["checks"]["timescaledb"] = {"ok": ts is not None, "version": ts}
    except Exception as exc:  # noqa: BLE001
        status_obj["checks"]["timescaledb"] = {"ok": False, "error": str(exc)}

    # ----- Latest heartbeat. -----
    try:
        latest = session.scalar(
            select(HeartbeatRow.timestamp).order_by(HeartbeatRow.timestamp.desc()).limit(1)
        )
        status_obj["checks"]["heartbeat"] = {
            "ok": latest is not None,
            "latest": latest.isoformat() if latest is not None else None,
        }
    except Exception as exc:  # noqa: BLE001
        status_obj["checks"]["heartbeat"] = {"ok": False, "error": str(exc)}

    # ----- Methods framework. -----
    in_memory = len(get_default_registry().list())
    try:
        db_count = session.scalar(select(func.count()).select_from(MethodRegistryRow))
        status_obj["checks"]["methods_framework"] = {
            "ok": True,
            "in_memory_registry_size": in_memory,
            "db_registry_size": int(db_count or 0),
        }
    except Exception as exc:  # noqa: BLE001
        status_obj["checks"]["methods_framework"] = {
            "ok": False,
            "error": str(exc),
            "in_memory_registry_size": in_memory,
        }

    status_obj["healthy"] = all(c.get("ok", False) for c in status_obj["checks"].values())
    return status_obj
