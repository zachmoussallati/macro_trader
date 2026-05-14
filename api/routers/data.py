"""Data-layer API routes.

GET /data/sources                 — list data sources + health
GET /data/freshness               — current freshness table
GET /data/lineage?source=...      — recent lineage records
GET /data/series                  — list series
GET /data/series/{id}/observations?as_of=YYYY-MM-DD
GET /data/series/{id}/latest?as_of=YYYY-MM-DD
GET /data/instruments             — list instruments
GET /data/instruments/{id}/bars?from=...&to=...&as_of=...
GET /data/quality/flags?as_of=...&method_id=...&instrument_id=...
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select

from api.deps import SessionDep
from macro_trader.db.models.macro_data import Series, SeriesObservation
from macro_trader.db.models.market_data import DailyBar, Instrument
from macro_trader.db.models.system import (
    DataFreshness,
    DataLineage,
    DataQualityFlag,
    DataSource,
)
from macro_trader.utils.dates import utcnow

router = APIRouter(prefix="/data", tags=["data"])


# =====================================================================
# Pydantic shapes
# =====================================================================
class SourceOut(BaseModel):
    source_id: str
    name: str
    base_url: str | None
    requires_auth: bool
    is_healthy: bool
    last_health_check: datetime | None
    rate_limit_notes: str | None

    model_config = {"from_attributes": True}


class FreshnessOut(BaseModel):
    source_id: str
    series_or_table: str
    expected_frequency: str
    last_successful_at: datetime | None
    last_attempted_at: datetime | None
    is_stale: bool
    consecutive_failures: int

    model_config = {"from_attributes": True}


class LineageOut(BaseModel):
    lineage_id: uuid.UUID
    source_id: str
    fetched_at: datetime
    fetch_method: str | None
    rows_ingested: int | None
    rows_updated: int | None
    rows_rejected: int | None
    error_count: int
    dagster_run_id: str | None
    dagster_asset_key: str | None

    model_config = {"from_attributes": True}


class SeriesOut(BaseModel):
    series_id: str
    name: str
    source: str
    frequency: str
    units: str | None
    seasonal_adjustment: str | None
    category: str | None
    affected_instruments: list[str]
    is_active: bool

    model_config = {"from_attributes": True}


class ObservationOut(BaseModel):
    series_id: str
    value_ts: datetime
    observation_ts: datetime
    value: float | None
    realtime_start: datetime | None
    realtime_end: datetime | None
    is_initial: bool
    revision_number: int


class InstrumentOut(BaseModel):
    instrument_id: str
    name: str
    asset_class: str
    sub_class: str | None
    proxy_ticker: str | None
    exchange: str | None
    is_active: bool
    tracking_error_notes: str | None

    model_config = {"from_attributes": True}


class BarOut(BaseModel):
    instrument_id: str
    value_ts: datetime
    observation_ts: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    adjusted_close: float | None


class QualityFlagOut(BaseModel):
    flag_id: uuid.UUID
    method_id: str
    series_id: str
    value_ts: datetime
    value: float | None
    is_flagged: bool
    run_at: datetime

    model_config = {"from_attributes": True}


# =====================================================================
# Sources / freshness / lineage
# =====================================================================
@router.get("/sources", response_model=list[SourceOut])
def list_sources(session: SessionDep) -> list[DataSource]:
    return list(session.scalars(select(DataSource).order_by(DataSource.source_id)))


@router.get("/freshness", response_model=list[FreshnessOut])
def list_freshness(session: SessionDep) -> list[DataFreshness]:
    return list(
        session.scalars(
            select(DataFreshness).order_by(DataFreshness.source_id, DataFreshness.series_or_table)
        )
    )


@router.get("/lineage", response_model=list[LineageOut])
def list_lineage(
    session: SessionDep,
    source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
) -> list[DataLineage]:
    stmt = select(DataLineage).order_by(DataLineage.fetched_at.desc()).limit(limit)
    if source is not None:
        stmt = stmt.where(DataLineage.source_id == source)
    return list(session.scalars(stmt))


# =====================================================================
# Series
# =====================================================================
@router.get("/series", response_model=list[SeriesOut])
def list_series_endpoint(
    session: SessionDep,
    source: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> list[Series]:
    stmt = select(Series).where(Series.is_active.is_(True)).order_by(Series.series_id)
    if source is not None:
        stmt = stmt.where(Series.source == source)
    if category is not None:
        stmt = stmt.where(Series.category == category)
    return list(session.scalars(stmt))


@router.get("/series/{series_id}/observations", response_model=list[ObservationOut])
def list_series_observations(
    series_id: str,
    session: SessionDep,
    as_of: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    """Latest vintage per value_ts, optionally as-of a point in time."""
    target = as_of or utcnow()
    stmt = (
        select(SeriesObservation)
        .where(SeriesObservation.series_id == series_id)
        .where(
            or_(
                SeriesObservation.realtime_start.is_(None),
                SeriesObservation.realtime_start <= target,
            )
        )
        .where(
            or_(
                SeriesObservation.realtime_end.is_(None),
                SeriesObservation.realtime_end >= target,
            )
        )
        .order_by(SeriesObservation.value_ts.desc())
        .limit(limit)
    )
    rows = list(session.scalars(stmt))
    return [
        {
            "series_id": r.series_id,
            "value_ts": r.value_ts,
            "observation_ts": r.observation_ts,
            "value": float(r.value) if r.value is not None else None,
            "realtime_start": r.realtime_start,
            "realtime_end": r.realtime_end,
            "is_initial": r.is_initial,
            "revision_number": r.revision_number,
        }
        for r in rows
    ]


@router.get("/series/{series_id}/latest", response_model=ObservationOut)
def latest_series_observation(
    series_id: str,
    session: SessionDep,
    as_of: datetime | None = Query(default=None),
) -> dict[str, Any]:
    target = as_of or utcnow()
    stmt = (
        select(SeriesObservation)
        .where(SeriesObservation.series_id == series_id)
        .where(
            or_(
                SeriesObservation.realtime_start.is_(None),
                SeriesObservation.realtime_start <= target,
            )
        )
        .where(
            or_(
                SeriesObservation.realtime_end.is_(None),
                SeriesObservation.realtime_end >= target,
            )
        )
        .order_by(SeriesObservation.value_ts.desc())
        .limit(1)
    )
    r = session.execute(stmt).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no observation")
    return {
        "series_id": r.series_id,
        "value_ts": r.value_ts,
        "observation_ts": r.observation_ts,
        "value": float(r.value) if r.value is not None else None,
        "realtime_start": r.realtime_start,
        "realtime_end": r.realtime_end,
        "is_initial": r.is_initial,
        "revision_number": r.revision_number,
    }


# =====================================================================
# Instruments + bars
# =====================================================================
@router.get("/instruments", response_model=list[InstrumentOut])
def list_instruments_endpoint(
    session: SessionDep,
    asset_class: str | None = Query(default=None),
    active_only: bool = Query(default=True),
) -> list[Instrument]:
    stmt = select(Instrument).order_by(Instrument.instrument_id)
    if asset_class is not None:
        stmt = stmt.where(Instrument.asset_class == asset_class)
    if active_only:
        stmt = stmt.where(Instrument.is_active.is_(True))
    return list(session.scalars(stmt))


@router.get("/instruments/{instrument_id}/bars", response_model=list[BarOut])
def list_bars(
    instrument_id: str,
    session: SessionDep,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    as_of: datetime | None = Query(default=None),
    limit: int = Query(default=365, ge=1, le=5000),
) -> list[dict[str, Any]]:
    """Daily bars for an instrument. ``as_of`` selects vintage."""
    stmt = select(DailyBar).where(DailyBar.instrument_id == instrument_id)
    if from_ is not None:
        stmt = stmt.where(DailyBar.value_ts >= from_)
    if to is not None:
        stmt = stmt.where(DailyBar.value_ts < to)
    if as_of is not None:
        stmt = stmt.where(DailyBar.observation_ts <= as_of)
    stmt = stmt.order_by(DailyBar.value_ts.desc()).limit(limit)
    rows = list(session.scalars(stmt))
    return [
        {
            "instrument_id": r.instrument_id,
            "value_ts": r.value_ts,
            "observation_ts": r.observation_ts,
            "open": float(r.open) if r.open is not None else None,
            "high": float(r.high) if r.high is not None else None,
            "low": float(r.low) if r.low is not None else None,
            "close": float(r.close) if r.close is not None else None,
            "volume": float(r.volume) if r.volume is not None else None,
            "adjusted_close": float(r.adjusted_close) if r.adjusted_close is not None else None,
        }
        for r in rows
    ]


# =====================================================================
# Quality
# =====================================================================
@router.get("/quality/flags", response_model=list[QualityFlagOut])
def list_quality_flags(
    session: SessionDep,
    method_id: str | None = Query(default=None),
    series_id: str | None = Query(default=None),
    instrument_id: str | None = Query(default=None),
    as_of: datetime | None = Query(default=None),
    lookback_days: int = Query(default=7, ge=1, le=365),
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[DataQualityFlag]:
    target = as_of or utcnow()
    start = target - timedelta(days=lookback_days)
    stmt = (
        select(DataQualityFlag)
        .where(and_(DataQualityFlag.run_at >= start, DataQualityFlag.run_at <= target))
        .order_by(DataQualityFlag.run_at.desc(), DataQualityFlag.value_ts.desc())
        .limit(limit)
    )
    if method_id:
        stmt = stmt.where(DataQualityFlag.method_id == method_id)
    if series_id:
        stmt = stmt.where(DataQualityFlag.series_id == series_id)
    if instrument_id:
        stmt = stmt.where(
            DataQualityFlag.series_id == f"market_data.daily_bars:{instrument_id}:close"
        )
    return list(session.scalars(stmt))


@router.get("/quality/summary", response_model=dict[str, int])
def quality_summary(
    session: SessionDep,
    lookback_hours: int = Query(default=24, ge=1, le=720),
) -> dict[str, int]:
    """Count of flags in the last `lookback_hours` per method."""
    cutoff = utcnow() - timedelta(hours=lookback_hours)
    rows = session.execute(
        select(DataQualityFlag.method_id, func.count())
        .where(DataQualityFlag.run_at >= cutoff)
        .group_by(DataQualityFlag.method_id)
    ).all()
    return {method_id: int(count) for method_id, count in rows}
