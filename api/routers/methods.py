"""Methods registry + comparison API.

Exposes the cross-cutting methods framework to the dashboard:

- ``GET /methods``                              list registered methods
- ``GET /methods/{method_id}``                  detail
- ``GET /methods/{method_id}/history``          status history
- ``GET /methods/comparisons``                  list comparison runs
- ``GET /methods/comparisons/{comparison_id}``  detail
- ``POST /methods/{method_id}/status``          (admin) change status with reason
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import AdminDep, SessionDep
from macro_trader.db.models.system import (
    MethodComparisonRow,
    MethodRegistryRow,
    MethodStatusHistoryRow,
)
from macro_trader.methods.registry import get_default_registry
from macro_trader.methods.status import MethodStatus
from macro_trader.utils.dates import utcnow

router = APIRouter(prefix="/methods", tags=["methods"])


# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class MethodOut(BaseModel):
    method_id: str
    component: str
    name: str
    version: str
    status: MethodStatus
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    status_changed_at: datetime
    status_reason: str


class MethodStatusHistoryOut(BaseModel):
    id: uuid.UUID
    method_id: str
    old_status: MethodStatus | None
    new_status: MethodStatus
    changed_at: datetime
    reason: str


class ComparisonOut(BaseModel):
    comparison_id: str
    component: str
    method_a_id: str
    method_b_id: str
    period_start: datetime
    period_end: datetime
    metrics: dict[str, Any]
    agreement: dict[str, Any]
    stability: dict[str, Any]
    notes: str
    created_at: datetime


class SetStatusRequest(BaseModel):
    status: MethodStatus
    reason: str = Field(min_length=1, max_length=2000)


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
def _row_to_method_out(row: MethodRegistryRow) -> MethodOut:
    return MethodOut(
        method_id=row.method_id,
        component=row.component,
        name=row.name,
        version=row.version,
        status=row.status,
        metadata=row.metadata_json or {},
        created_at=row.created_at,
        status_changed_at=row.status_changed_at,
        status_reason=row.status_reason,
    )


@router.get("", response_model=list[MethodOut])
def list_methods_endpoint(
    session: SessionDep,
    component: str | None = Query(default=None),
    method_status: MethodStatus | None = Query(default=None, alias="status"),
) -> list[MethodOut]:
    stmt = select(MethodRegistryRow)
    if component is not None:
        stmt = stmt.where(MethodRegistryRow.component == component)
    if method_status is not None:
        stmt = stmt.where(MethodRegistryRow.status == method_status)
    rows = session.scalars(stmt.order_by(MethodRegistryRow.created_at)).all()
    return [_row_to_method_out(r) for r in rows]


@router.get("/comparisons", response_model=list[ComparisonOut])
def list_comparisons(
    session: SessionDep,
    component: str | None = Query(default=None),
    method_a_id: str | None = Query(default=None),
    method_b_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[ComparisonOut]:
    stmt = select(MethodComparisonRow)
    if component is not None:
        stmt = stmt.where(MethodComparisonRow.component == component)
    if method_a_id is not None:
        stmt = stmt.where(MethodComparisonRow.method_a_id == method_a_id)
    if method_b_id is not None:
        stmt = stmt.where(MethodComparisonRow.method_b_id == method_b_id)
    stmt = stmt.order_by(MethodComparisonRow.created_at.desc()).limit(limit)
    rows = session.scalars(stmt).all()
    return [ComparisonOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/comparisons/{comparison_id}", response_model=ComparisonOut)
def get_comparison(comparison_id: str, session: SessionDep) -> ComparisonOut:
    row = session.get(MethodComparisonRow, comparison_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="comparison not found")
    return ComparisonOut.model_validate(row, from_attributes=True)


@router.get("/{method_id}", response_model=MethodOut)
def get_method_endpoint(method_id: str, session: SessionDep) -> MethodOut:
    row = session.get(MethodRegistryRow, method_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="method not found")
    return _row_to_method_out(row)


@router.get("/{method_id}/history", response_model=list[MethodStatusHistoryOut])
def get_method_history(method_id: str, session: SessionDep) -> list[MethodStatusHistoryOut]:
    if session.get(MethodRegistryRow, method_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="method not found")
    stmt = (
        select(MethodStatusHistoryRow)
        .where(MethodStatusHistoryRow.method_id == method_id)
        .order_by(MethodStatusHistoryRow.changed_at)
    )
    rows = session.scalars(stmt).all()
    return [MethodStatusHistoryOut.model_validate(r, from_attributes=True) for r in rows]


@router.post("/{method_id}/status", response_model=MethodOut)
def set_method_status(
    method_id: str,
    payload: SetStatusRequest,
    session: SessionDep,
    _admin: AdminDep,
) -> MethodOut:
    row = session.get(MethodRegistryRow, method_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="method not found")
    old_status = row.status
    new_status = payload.status
    if new_status == old_status:
        return _row_to_method_out(row)
    row.status = new_status
    row.status_changed_at = utcnow()
    row.status_reason = payload.reason
    session.add(
        MethodStatusHistoryRow(
            method_id=method_id,
            old_status=old_status,
            new_status=new_status,
            changed_at=row.status_changed_at,
            reason=payload.reason,
        )
    )
    # Reflect in in-process registry if loaded. DB is authoritative if the
    # method isn't loaded in this process.
    import contextlib

    registry = get_default_registry()
    with contextlib.suppress(KeyError):
        registry.set_status(method_id, new_status, reason=payload.reason)
    session.commit()
    session.refresh(row)
    return _row_to_method_out(row)
