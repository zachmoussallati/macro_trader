"""Methods registry.

Two-tier design:

1. **In-process registry** (``MethodRegistry``) — a thread-safe dict of
   ``Method`` instances keyed by ``method_id``. Used at runtime to fetch the
   method that drives a component or to enumerate shadows.

2. **Database mirror** (``system.methods_registry`` + ``system.method_status_history``)
   — durable record of every method that has been registered, its metadata,
   its current status, the full status history with reasons, and (optionally)
   a serialized blob of fitted state. The DB is the source of truth across
   process restarts; the in-process registry is a hot cache.

The functions in this module operate on the module-level singleton registry.
Tests can construct a fresh ``MethodRegistry`` directly.

Note: DB sync is best-effort. If a DB session is not provided (e.g. during
unit tests), state changes are kept in-memory only. Production callers should
always pass a session or use the FastAPI dependency that injects one.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from macro_trader.methods.base import Method, MethodMetadata
from macro_trader.methods.status import MethodStatus
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class MethodAlreadyRegisteredError(KeyError):
    """Raised when registering a method whose `method_id` is already present."""


class MethodNotFoundError(KeyError):
    """Raised when a `method_id` is not in the registry."""


class MethodRegistry:
    """In-process, thread-safe registry mirrored to the DB.

    The registry tracks both the method instance and its current status.
    Status transitions write a row to ``system.method_status_history`` and
    update the row in ``system.methods_registry`` if a SQLAlchemy session is
    available.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._methods: dict[str, Method[Any, Any]] = {}
        self._statuses: dict[str, MethodStatus] = {}

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    def register(
        self,
        method: Method[Any, Any],
        status: MethodStatus,
        *,
        session: Session | None = None,
        reason: str = "initial registration",
    ) -> None:
        """Register a method. Idempotent only if metadata matches exactly."""
        method_id = method.metadata.method_id
        with self._lock:
            if method_id in self._methods:
                existing = self._methods[method_id]
                if existing.metadata != method.metadata:
                    raise MethodAlreadyRegisteredError(
                        f"method_id {method_id!r} already registered with different metadata"
                    )
                # Idempotent: same metadata, possibly status update.
                if self._statuses[method_id] != status:
                    self.set_status(method_id, status, reason=reason, session=session)
                return

            self._methods[method_id] = method
            self._statuses[method_id] = status

        if session is not None:
            self._db_upsert(method, status, reason, session)

    def get(self, method_id: str) -> Method[Any, Any]:
        with self._lock:
            try:
                return self._methods[method_id]
            except KeyError as exc:
                raise MethodNotFoundError(method_id) from exc

    def status_of(self, method_id: str) -> MethodStatus:
        with self._lock:
            try:
                return self._statuses[method_id]
            except KeyError as exc:
                raise MethodNotFoundError(method_id) from exc

    def list_methods(
        self,
        *,
        component: str | None = None,
        status: MethodStatus | None = None,
    ) -> list[MethodMetadata]:
        with self._lock:
            results: list[MethodMetadata] = []
            for mid, method in self._methods.items():
                if component is not None and method.metadata.component != component:
                    continue
                if status is not None and self._statuses[mid] != status:
                    continue
                results.append(method.metadata)
            return results

    def set_status(
        self,
        method_id: str,
        status: MethodStatus,
        *,
        reason: str,
        session: Session | None = None,
    ) -> None:
        """Change a method's status, recording the transition in the history."""
        with self._lock:
            if method_id not in self._methods:
                raise MethodNotFoundError(method_id)
            old = self._statuses[method_id]
            if old == status:
                return
            self._statuses[method_id] = status

            if status == MethodStatus.PRODUCTION:
                self._demote_existing_production(
                    component=self._methods[method_id].metadata.component,
                    keep=method_id,
                    reason=f"superseded by {method_id}",
                    session=session,
                )

        if session is not None:
            self._db_update_status(method_id, old, status, reason, session)

    def production_for(self, component: str) -> Method[Any, Any]:
        """Return the method currently driving decisions for `component`.

        Preference: PRODUCTION (a promoted enhancement) over BASELINE. Raises
        ``MethodNotFoundError`` if neither exists for the component.
        """
        with self._lock:
            prod = self._find_one(component, MethodStatus.PRODUCTION)
            if prod is not None:
                return prod
            base = self._find_one(component, MethodStatus.BASELINE)
            if base is not None:
                return base
            raise MethodNotFoundError(f"no driving method for component {component!r}")

    def shadows_for(self, component: str) -> list[Method[Any, Any]]:
        with self._lock:
            return [
                m
                for mid, m in self._methods.items()
                if m.metadata.component == component and self._statuses[mid] == MethodStatus.SHADOW
            ]

    def clear(self) -> None:
        """Test-only: wipe the in-process state. Does not touch the DB."""
        with self._lock:
            self._methods.clear()
            self._statuses.clear()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _find_one(self, component: str, status: MethodStatus) -> Method[Any, Any] | None:
        for mid, method in self._methods.items():
            if method.metadata.component == component and self._statuses[mid] == status:
                return method
        return None

    def _demote_existing_production(
        self,
        *,
        component: str,
        keep: str,
        reason: str,
        session: Session | None,
    ) -> None:
        """Ensure at most one PRODUCTION per component. Demote any other to DEPRECATED."""
        for mid, method in list(self._methods.items()):
            if mid == keep:
                continue
            if (
                method.metadata.component == component
                and self._statuses[mid] == MethodStatus.PRODUCTION
            ):
                old = self._statuses[mid]
                self._statuses[mid] = MethodStatus.DEPRECATED
                if session is not None:
                    self._db_update_status(mid, old, MethodStatus.DEPRECATED, reason, session)

    def _db_upsert(
        self,
        method: Method[Any, Any],
        status: MethodStatus,
        reason: str,
        session: Session,
    ) -> None:
        from macro_trader.db.models.system import MethodRegistryRow, MethodStatusHistoryRow

        now = utcnow()
        row = session.get(MethodRegistryRow, method.metadata.method_id)
        if row is None:
            row = MethodRegistryRow(
                method_id=method.metadata.method_id,
                component=method.metadata.component,
                name=method.metadata.name,
                version=method.metadata.version,
                status=status,
                metadata_json={
                    "description": method.metadata.description,
                    "references": method.metadata.references,
                },
                created_at=now,
                status_changed_at=now,
                status_reason=reason,
            )
            session.add(row)
            session.add(
                MethodStatusHistoryRow(
                    method_id=method.metadata.method_id,
                    old_status=None,
                    new_status=status,
                    changed_at=now,
                    reason=reason,
                )
            )
        else:
            row.component = method.metadata.component
            row.name = method.metadata.name
            row.version = method.metadata.version
            row.metadata_json = {
                "description": method.metadata.description,
                "references": method.metadata.references,
            }
        session.flush()

    def _db_update_status(
        self,
        method_id: str,
        old_status: MethodStatus,
        new_status: MethodStatus,
        reason: str,
        session: Session,
    ) -> None:
        from macro_trader.db.models.system import MethodRegistryRow, MethodStatusHistoryRow

        now = utcnow()
        row = session.get(MethodRegistryRow, method_id)
        if row is None:
            raise MethodNotFoundError(method_id)
        row.status = new_status
        row.status_changed_at = now
        row.status_reason = reason
        session.add(
            MethodStatusHistoryRow(
                method_id=method_id,
                old_status=old_status,
                new_status=new_status,
                changed_at=now,
                reason=reason,
            )
        )
        session.flush()


# ----------------------------------------------------------------------
# Module-level singleton + convenience functions
# ----------------------------------------------------------------------
_registry = MethodRegistry()


def get_default_registry() -> MethodRegistry:
    return _registry


def register_method(
    method: Method[Any, Any],
    status: MethodStatus,
    *,
    session: Session | None = None,
    reason: str = "initial registration",
) -> None:
    _registry.register(method, status, session=session, reason=reason)


def get_method(method_id: str) -> Method[Any, Any]:
    return _registry.get(method_id)


def list_methods(
    component: str | None = None,
    status: MethodStatus | None = None,
) -> list[MethodMetadata]:
    return _registry.list_methods(component=component, status=status)


def set_status(
    method_id: str,
    status: MethodStatus,
    reason: str,
    *,
    session: Session | None = None,
) -> None:
    _registry.set_status(method_id, status, reason=reason, session=session)


def get_production(component: str) -> Method[Any, Any]:
    return _registry.production_for(component)


def get_shadows(component: str) -> list[Method[Any, Any]]:
    return _registry.shadows_for(component)
