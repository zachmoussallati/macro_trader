"""yfinance options-chain ingester.

Uses ``yf.Ticker(symbol).option_chain(expiry).calls/.puts``. yfinance
populates ``impliedVolatility`` but quality varies; we re-compute IV
via Black-Scholes (using the mid-quote when bid+ask are both present,
otherwise ``last``) whenever the provider value is obviously wrong
(non-finite, <= 0.01, > 5.0). Greeks are never provided by yfinance
and are always computed from BS after IV is determined.

Stage 5 keeps a 0.5-second sleep between ticker fetches to be polite
to yfinance's informal rate limit. Six tickers x 1 chain each = ~5s
per daily run -- acceptable.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from macro_trader.logging_setup import get_logger
from macro_trader.signals.vol_surface.ingestion.base import (
    ChainRow,
    OptionsChainIngester,
)
from macro_trader.signals.vol_surface.pricing import greeks, implied_vol

if TYPE_CHECKING:
    pass

log = get_logger(__name__)


_SLEEP_BETWEEN_TICKERS = 0.5  # seconds
_DEFAULT_R = 0.045  # rough US risk-free; tighten later if needed
_DEFAULT_Q = 0.0   # treat ETFs as zero-dividend for vol surface purposes
_IV_MIN, _IV_MAX = 0.01, 5.0


class YfinanceOptionsIngester(OptionsChainIngester):
    """yfinance-backed options-chain provider."""

    def __init__(self) -> None:
        super().__init__(source="yfinance")

    def fetch_chain(
        self,
        instrument_id: str,
        *,
        proxy_ticker: str,
        now: datetime,
    ) -> list[ChainRow]:
        import yfinance as yf

        ticker = yf.Ticker(proxy_ticker)
        try:
            expiries = list(ticker.options or [])
            spot = self._spot_from_ticker(ticker)
        except Exception as exc:  # pragma: no cover - yfinance flake
            log.warning(
                "vol_surface.ingest.expiries_failed",
                instrument=instrument_id,
                ticker=proxy_ticker,
                error=str(exc),
            )
            return []
        if not expiries or spot is None or spot <= 0:
            return []

        rows: list[ChainRow] = []
        snapshot_ts = now.astimezone(UTC)
        for expiry_str in expiries:
            try:
                chain = ticker.option_chain(expiry_str)
            except Exception as exc:  # pragma: no cover
                log.warning(
                    "vol_surface.ingest.chain_failed",
                    instrument=instrument_id,
                    expiry=expiry_str,
                    error=str(exc),
                )
                continue
            expiry_ts = datetime.fromisoformat(expiry_str).replace(tzinfo=UTC)
            t_years = max(
                (expiry_ts - snapshot_ts).total_seconds() / (365.0 * 86400.0), 1e-6
            )
            for option_type, frame in (("call", chain.calls), ("put", chain.puts)):
                for _, raw in frame.iterrows():
                    rows.append(
                        self._build_row(
                            raw,
                            instrument_id=instrument_id,
                            snapshot_ts=snapshot_ts,
                            expiry_ts=expiry_ts,
                            t_years=t_years,
                            option_type=option_type,
                            spot=spot,
                        )
                    )
        time.sleep(_SLEEP_BETWEEN_TICKERS)
        return rows

    def _spot_from_ticker(self, ticker: Any) -> float | None:
        info = getattr(ticker, "fast_info", None)
        if info is not None:
            last = info.get("last_price") if isinstance(info, dict) else getattr(
                info, "last_price", None
            )
            if last is not None and last > 0:
                return float(last)
        # Fallback: most-recent daily close.
        history = ticker.history(period="5d")
        if history is None or history.empty:
            return None
        last_close = history["Close"].dropna()
        if last_close.empty:
            return None
        return float(last_close.iloc[-1])

    def _build_row(
        self,
        raw: Any,
        *,
        instrument_id: str,
        snapshot_ts: datetime,
        expiry_ts: datetime,
        t_years: float,
        option_type: str,
        spot: float,
    ) -> ChainRow:
        strike = float(raw["strike"])
        bid = _maybe_float(raw.get("bid"))
        ask = _maybe_float(raw.get("ask"))
        last = _maybe_float(raw.get("lastPrice"))
        volume = _maybe_int(raw.get("volume"))
        oi = _maybe_int(raw.get("openInterest"))
        provider_iv = _maybe_float(raw.get("impliedVolatility"))

        mid = None
        if bid is not None and ask is not None and bid > 0 and ask >= bid:
            mid = 0.5 * (bid + ask)
        elif last is not None and last > 0:
            mid = last

        iv = provider_iv if _iv_ok(provider_iv) else None
        if iv is None and mid is not None:
            iv = implied_vol(
                price=mid,
                spot=spot,
                strike=strike,
                t=t_years,
                r=_DEFAULT_R,
                q=_DEFAULT_Q,
                option_type=option_type,
            )

        g = greeks(
            spot=spot,
            strike=strike,
            t=t_years,
            r=_DEFAULT_R,
            q=_DEFAULT_Q,
            sigma=iv if iv and _iv_ok(iv) else 0.3,
            option_type=option_type,
        )

        return ChainRow(
            instrument_id=instrument_id,
            snapshot_ts=snapshot_ts,
            expiry_ts=expiry_ts,
            strike=strike,
            option_type=option_type,
            bid=bid,
            ask=ask,
            last=last,
            volume=volume,
            open_interest=oi,
            implied_vol=iv if _iv_ok(iv) else None,
            delta=g.delta,
            gamma=g.gamma,
            vega=g.vega,
            theta=g.theta,
            underlying_price=spot,
            source=self.source,
        )


def _maybe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # NaN check


def _maybe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _iv_ok(iv: float | None) -> bool:
    return iv is not None and _IV_MIN <= iv <= _IV_MAX


__all__ = ["YfinanceOptionsIngester"]
