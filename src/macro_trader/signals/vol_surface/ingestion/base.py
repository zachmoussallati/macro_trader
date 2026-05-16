"""Abstract base for options-chain ingesters.

Stage 5 ships only :class:`macro_trader.signals.vol_surface.ingestion.yfinance.YfinanceOptionsIngester`,
but the schema, surface fitting, signal extraction, comparator, and
dashboard are deliberately source-agnostic so paid-data ingesters
slot in as additional subclasses post-v1.

Subclasses implement :meth:`fetch_chain` which returns a list of
:class:`ChainRow` per (instrument, snapshot, expiry, strike,
option_type). The base class handles upserting into
``market_data.options_chains`` and (in Stage 5) does not currently
write to a lineage table — alt-data ingestion is invoked via the
standard data-ingestion path through :class:`macro_trader.data.ingestion.base.Ingester`
when the options ingest is wired into Dagster (Stage 6+).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass(slots=True, frozen=True)
class ChainRow:
    """Provider-agnostic chain row. ``implied_vol`` and Greeks may be
    None if the provider didn't supply them — :meth:`backfill_greeks`
    on the consumer side will compute them via Black-Scholes."""

    instrument_id: str
    snapshot_ts: datetime
    expiry_ts: datetime
    strike: float
    option_type: str  # 'call' or 'put'
    bid: float | None
    ask: float | None
    last: float | None
    volume: int | None
    open_interest: int | None
    implied_vol: float | None
    delta: float | None
    gamma: float | None
    vega: float | None
    theta: float | None
    underlying_price: float | None
    source: str


class OptionsChainIngester(ABC):
    """Abstract base. Subclass per provider."""

    source: str

    def __init__(self, *, source: str) -> None:
        if not source:
            raise ValueError("source must be non-empty")
        self.source = source

    @abstractmethod
    def fetch_chain(
        self, instrument_id: str, *, proxy_ticker: str, now: datetime
    ) -> list[ChainRow]:
        """Return the current options chain for an instrument.

        Implementations should respect provider rate limits internally.
        """
        raise NotImplementedError


__all__ = ["ChainRow", "OptionsChainIngester"]
