"""Health-check endpoint.

Reports:
- service liveness
- DB reachability + presence of `system.heartbeat`
- methods framework status (registry size + DB row count)
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select, text

from api.deps import SessionDep
from macro_trader import __version__
from macro_trader.db.models.system import (
    DataFreshness,
    DataQualityFlag,
    HeartbeatRow,
    MethodRegistryRow,
)
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
    except Exception as exc:
        status_obj["checks"]["database"] = {"ok": False, "error": str(exc)}

    # ----- TimescaleDB presence. -----
    try:
        ts = session.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
        ).scalar()
        status_obj["checks"]["timescaledb"] = {"ok": ts is not None, "version": ts}
    except Exception as exc:
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
    except Exception as exc:
        status_obj["checks"]["heartbeat"] = {"ok": False, "error": str(exc)}

    # ----- Methods framework. -----
    in_memory = len(get_default_registry().list_methods())
    try:
        db_count = session.scalar(select(func.count()).select_from(MethodRegistryRow))
        status_obj["checks"]["methods_framework"] = {
            "ok": True,
            "in_memory_registry_size": in_memory,
            "db_registry_size": int(db_count or 0),
        }
    except Exception as exc:
        status_obj["checks"]["methods_framework"] = {
            "ok": False,
            "error": str(exc),
            "in_memory_registry_size": in_memory,
        }

    # ----- Data freshness summary. -----
    try:
        freshness_rows = list(session.scalars(select(DataFreshness)))
        total = len(freshness_rows)
        stale = sum(1 for r in freshness_rows if r.is_stale)
        status_obj["checks"]["data_freshness"] = {
            "ok": stale == 0,
            "total": total,
            "stale": stale,
        }
    except Exception as exc:
        status_obj["checks"]["data_freshness"] = {"ok": False, "error": str(exc)}

    # ----- Data quality summary (flags in the last 24h). -----
    try:
        cutoff = utcnow() - timedelta(hours=24)
        flags_24h = session.scalar(
            select(func.count())
            .select_from(DataQualityFlag)
            .where(DataQualityFlag.run_at >= cutoff)
        )
        status_obj["checks"]["data_quality"] = {
            "ok": True,
            "flags_last_24h": int(flags_24h or 0),
        }
    except Exception as exc:
        status_obj["checks"]["data_quality"] = {"ok": False, "error": str(exc)}

    status_obj["healthy"] = all(c.get("ok", False) for c in status_obj["checks"].values())
    return status_obj
