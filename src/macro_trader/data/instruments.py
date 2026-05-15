"""Instrument-master operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from macro_trader.db.models.market_data import Instrument
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def upsert_instrument(
    session: Session,
    *,
    instrument_id: str,
    name: str,
    asset_class: str,
    sub_class: str | None = None,
    proxy_ticker: str | None = None,
    proxy_type: str | None = None,
    underlying_ref: str | None = None,
    currency: str = "USD",
    exchange: str | None = None,
    is_active: bool = True,
    tracking_error_notes: str | None = None,
    metadata: dict[str, object] | None = None,
) -> Instrument:
    """Insert or update an instrument row. Idempotent on instrument_id."""
    row = session.get(Instrument, instrument_id)
    now = utcnow()
    if row is None:
        row = Instrument(
            instrument_id=instrument_id,
            name=name,
            asset_class=asset_class,
            sub_class=sub_class,
            proxy_ticker=proxy_ticker,
            proxy_type=proxy_type,
            underlying_ref=underlying_ref,
            currency=currency,
            exchange=exchange,
            is_active=is_active,
            tracking_error_notes=tracking_error_notes,
            instrument_metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.name = name
        row.asset_class = asset_class
        row.sub_class = sub_class
        row.proxy_ticker = proxy_ticker
        row.proxy_type = proxy_type
        row.underlying_ref = underlying_ref
        row.currency = currency
        row.exchange = exchange
        row.is_active = is_active
        row.tracking_error_notes = tracking_error_notes
        if metadata is not None:
            row.instrument_metadata = dict(metadata)
        row.updated_at = now
    session.flush()
    return row


def list_instruments(
    session: Session,
    *,
    asset_class: str | None = None,
    active_only: bool = True,
) -> list[Instrument]:
    stmt = select(Instrument).order_by(Instrument.instrument_id)
    if asset_class is not None:
        stmt = stmt.where(Instrument.asset_class == asset_class)
    if active_only:
        stmt = stmt.where(Instrument.is_active.is_(True))
    return list(session.scalars(stmt))


def get_class_groups(
    session: Session,
    instrument_ids: list[str],
    *,
    column: str = "asset_class",
) -> dict[str, list[str]]:
    """Group ``instrument_ids`` by their value in the named class column.

    ``column`` is ``"asset_class"`` (default — coarse: energy /
    base_metals / precious_metals / agriculture) or ``"sub_class"``
    (fine: crude_oil / natural_gas / distillate / gasoline / copper /
    aluminum / gold / silver / platinum / grains / oilseeds).

    Returns a ``{group_label: [instrument_id, ...]}`` mapping. Each
    group's id list preserves the input order. Instruments whose row
    is missing or whose class value is NULL are dropped silently —
    callers that need universe coverage should compare lengths.

    Cross-sectional value ranking uses ``asset_class`` so each group
    has at least 2 instruments to rank against; ``sub_class`` is too
    fine-grained for that purpose (e.g. a "natural_gas" group of one
    cannot be ranked). See notes/stage_4a/decisions.md.
    """
    if not instrument_ids:
        return {}
    if column not in ("asset_class", "sub_class"):
        raise ValueError(f"column must be 'asset_class' or 'sub_class', got {column!r}")

    rows = list(
        session.scalars(
            select(Instrument).where(Instrument.instrument_id.in_(instrument_ids))
        )
    )
    by_id = {r.instrument_id: r for r in rows}

    out: dict[str, list[str]] = {}
    for instrument_id in instrument_ids:
        inst = by_id.get(instrument_id)
        if inst is None:
            continue
        label = getattr(inst, column)
        if label is None:
            continue
        out.setdefault(label, []).append(instrument_id)
    return out
