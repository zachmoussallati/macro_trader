"""yfinance ingester for ETF daily bars.

Fetches OHLCV for every instrument's ETF proxy. UPSERT into
``market_data.daily_bars`` keyed on ``(instrument_id, value_ts,
observation_ts)``. ``observation_ts`` is when we fetched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from macro_trader.data.ingestion.base import Ingester
from macro_trader.data.lineage import IngestStats, LineageRecord
from macro_trader.db.models.market_data import DailyBar, Instrument
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class YFinanceRaw:
    bars: list[dict[str, Any]] = field(default_factory=list)


class YFinanceIngester(Ingester[YFinanceRaw]):
    source_id = "yfinance"
    series_or_table = "market_data.daily_bars"
    expected_frequency = "daily"
    fetch_method = "api"

    DEFAULT_LOOKBACK_DAYS = 30

    def fetch(self, *, since: datetime | None = None) -> YFinanceRaw:
        import yfinance as yf

        with self.session_factory() as session:
            instruments = list(
                session.scalars(
                    select(Instrument)
                    .where(Instrument.is_active.is_(True))
                    .where(Instrument.proxy_ticker.is_not(None))
                )
            )

        start = (
            since.astimezone(UTC)
            if since
            else utcnow() - timedelta(days=self.DEFAULT_LOOKBACK_DAYS)
        )
        end = utcnow()
        raw = YFinanceRaw()
        for instrument in instruments:
            try:
                df = self._fetch_one(yf, instrument.proxy_ticker, start=start, end=end)
            except Exception as exc:
                self.log.warning(
                    "ingest.yfinance.ticker_failed",
                    ticker=instrument.proxy_ticker,
                    error=str(exc),
                )
                continue
            for _, r in df.iterrows():
                raw.bars.append(
                    {
                        "instrument_id": instrument.instrument_id,
                        "ticker": instrument.proxy_ticker,
                        "value_ts": r.name.to_pydatetime().replace(tzinfo=UTC),
                        "open": _opt_float(r.get("Open")),
                        "high": _opt_float(r.get("High")),
                        "low": _opt_float(r.get("Low")),
                        "close": _opt_float(r.get("Close")),
                        "volume": _opt_float(r.get("Volume")),
                        "adjusted_close": _opt_float(r.get("Adj Close") or r.get("Close")),
                    }
                )
        return raw

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, max=30),
        reraise=True,
    )
    def _fetch_one(
        self,
        yf: Any,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
    ) -> Any:
        df = yf.download(
            ticker,
            start=start.date(),
            end=end.date(),
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        # yfinance returns multi-indexed columns for single tickers in newer
        # versions; flatten if so.
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = [c[0] for c in df.columns]
        return df.dropna(how="all")

    def transform(self, raw: YFinanceRaw) -> list[dict[str, Any]]:
        now = utcnow()
        out: list[dict[str, Any]] = []
        for bar in raw.bars:
            out.append(
                {
                    "instrument_id": bar["instrument_id"],
                    "value_ts": bar["value_ts"],
                    "observation_ts": now,
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["volume"],
                    "adjusted_close": bar["adjusted_close"],
                    "source": "yfinance",
                    "source_version": None,
                    "is_revised": False,
                }
            )
        return out

    def persist(
        self,
        session: Session,
        rows: list[dict[str, Any]],
        lineage: LineageRecord,
    ) -> IngestStats:
        stats = IngestStats()
        if not rows:
            return stats
        payload = [{**r, "lineage_id": lineage.lineage_id} for r in rows]
        stmt = pg_insert(DailyBar).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "value_ts", "observation_ts"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "adjusted_close": stmt.excluded.adjusted_close,
                "lineage_id": stmt.excluded.lineage_id,
            },
        )
        result = session.execute(stmt)
        stats.rows_ingested = result.rowcount or len(payload)
        return stats


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v
