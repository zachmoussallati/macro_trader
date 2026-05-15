"""Seed the 13 commodity instruments + ETF proxies.

Idempotent: upserts on ``instrument_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from macro_trader.data.instruments import upsert_instrument

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


# (instrument_id, name, asset_class, sub_class, proxy_ticker,
#  underlying_ref, exchange, tracking_error_notes)
INSTRUMENTS: tuple[tuple[str, str, str, str, str, str, str, str], ...] = (
    (
        "CL",
        "WTI Crude Oil",
        "energy",
        "crude_oil",
        "USO",
        "NYMEX:CL1!",
        "NYMEX",
        "USO suffers significant contango bleed; do not use for short-term return signals without adjustment.",
    ),
    (
        "BZ",
        "Brent Crude Oil",
        "energy",
        "crude_oil",
        "BNO",
        "ICE:BZ1!",
        "ICE",
        "BNO tracks Brent via futures; similar contango drag to USO.",
    ),
    (
        "NG",
        "Natural Gas",
        "energy",
        "refined_products",
        "UNG",
        "NYMEX:NG1!",
        "NYMEX",
        "UNG has historically been the worst-tracking ETF in this universe; use only for signals robust to large basis drift.",
    ),
    (
        "HO",
        "Heating Oil",
        "energy",
        "refined_products",
        "UHN",
        "NYMEX:HO1!",
        "NYMEX",
        "UHN has low volume; check liquidity before relying on intraday prints.",
    ),
    (
        "RB",
        "RBOB Gasoline",
        "energy",
        "refined_products",
        "UGA",
        "NYMEX:RB1!",
        "NYMEX",
        "UGA tracks NYMEX gasoline; rebalances cause modest tracking error.",
    ),
    (
        "HG",
        "Copper",
        "base_metals",
        "base_metals",
        "CPER",
        "COMEX:HG1!",
        "COMEX",
        "CPER tracks COMEX copper; mild contango drag.",
    ),
    (
        "ALI",
        "Aluminum",
        "base_metals",
        "base_metals",
        "JJU",
        "LME:AH1!",
        "LME",
        "JJU is an ETN with credit risk; LME alu lacks a clean ETF proxy.",
    ),
    (
        "GC",
        "Gold",
        "precious_metals",
        "precious_metals",
        "GLD",
        "COMEX:GC1!",
        "COMEX",
        "GLD is the cleanest precious-metals proxy in the universe (physical-backed, tight tracking).",
    ),
    (
        "SI",
        "Silver",
        "precious_metals",
        "precious_metals",
        "SLV",
        "COMEX:SI1!",
        "COMEX",
        "SLV is physical-backed; spreads widen during stress.",
    ),
    (
        "PL",
        "Platinum",
        "precious_metals",
        "precious_metals",
        "PPLT",
        "NYMEX:PL1!",
        "NYMEX",
        "PPLT is physical-backed; low ADV vs gold/silver ETFs.",
    ),
    (
        "ZC",
        "Corn",
        "agriculture",
        "grains",
        "CORN",
        "CBOT:ZC1!",
        "CBOT",
        "CORN has roll yield drag; switch to futures roll-adjusted series for backtests.",
    ),
    (
        "ZS",
        "Soybeans",
        "agriculture",
        "grains",
        "SOYB",
        "CBOT:ZS1!",
        "CBOT",
        "SOYB suffers similar contango drag as CORN.",
    ),
    (
        "ZW",
        "Wheat",
        "agriculture",
        "grains",
        "WEAT",
        "CBOT:ZW1!",
        "CBOT",
        "WEAT tracks CBOT wheat (not KC HRW); structural backwardation rare in this universe.",
    ),
)


def seed(session: Session) -> int:
    """Upsert all 13 instruments. Returns the count inserted/updated."""
    count = 0
    for (
        instrument_id,
        name,
        asset_class,
        sub_class,
        proxy_ticker,
        underlying_ref,
        exchange,
        tracking_error_notes,
    ) in INSTRUMENTS:
        upsert_instrument(
            session,
            instrument_id=instrument_id,
            name=name,
            asset_class=asset_class,
            sub_class=sub_class,
            proxy_ticker=proxy_ticker,
            proxy_type="etf",
            underlying_ref=underlying_ref,
            currency="USD",
            exchange=exchange,
            is_active=True,
            tracking_error_notes=tracking_error_notes,
        )
        count += 1
    session.commit()
    return count
