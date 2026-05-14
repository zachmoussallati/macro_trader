"""CFTC positioning (COT) models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from macro_trader.db.base import Base

POSITIONING = "positioning"


class COTWeekly(Base):
    """Weekly Commitments of Traders. Hypertable on `report_ts`.

    Stores all three report types (legacy, disaggregated, financial_tff)
    in a wide layout — columns null when the report type doesn't supply
    that breakdown.
    """

    __tablename__ = "cot_weekly"
    __table_args__ = (
        Index("ix_cot_weekly_instrument", "instrument_id"),
        Index("ix_cot_weekly_publication", "publication_ts"),
        {"schema": POSITIONING},
    )

    report_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True
    )
    instrument_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("market_data.instruments.instrument_id", ondelete="CASCADE"),
        primary_key=True,
    )
    report_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    publication_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    cftc_contract_code: Mapped[str] = mapped_column(String(16), nullable=False)
    open_interest: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    producer_long: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    producer_short: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    swap_long: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    swap_short: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    managed_money_long: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    managed_money_short: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    other_reportable_long: Mapped[float | None] = mapped_column(
        Numeric(24, 4), nullable=True
    )
    other_reportable_short: Mapped[float | None] = mapped_column(
        Numeric(24, 4), nullable=True
    )
    nonreportable_long: Mapped[float | None] = mapped_column(
        Numeric(24, 4), nullable=True
    )
    nonreportable_short: Mapped[float | None] = mapped_column(
        Numeric(24, 4), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="cftc")
    source_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
