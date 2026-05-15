"""Stage 4B verification: changing CrossSectionalValue.class_column
produces materially different per-instrument rank outputs.

Sub_class (post-reseed) splits energy into crude_oil + refined_products
and renames agriculture to grains. asset_class keeps the original
coarser groupings. Cross-sectional ranks within those groups must
differ when at least one group's membership changes.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from macro_trader.data.instruments import upsert_instrument
from macro_trader.data.seed import instruments_seed
from macro_trader.db.models.market_data import DailyBar
from macro_trader.signals.base import SignalInput
from macro_trader.signals.value.methods import CrossSectionalValue
from macro_trader.utils.dates import utcnow


def _seed_prices(session, instruments: list[str], n_days: int = 320) -> None:
    rng = np.random.default_rng(42)
    now = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    base = now - timedelta(days=n_days - 1)
    for i, inst in enumerate(instruments):
        # Engineered drift per instrument so the panel has cross-sectional
        # variance to rank against.
        drift = (i - len(instruments) / 2) * 0.001
        rets = rng.normal(loc=drift, scale=0.01, size=n_days)
        prices = 100.0 * np.exp(np.cumsum(rets))
        for d in range(n_days):
            session.add(
                DailyBar(
                    instrument_id=inst,
                    value_ts=base + timedelta(days=d),
                    observation_ts=now,
                    close=float(prices[d]),
                    source="test",
                )
            )
    session.flush()


@pytest.mark.integration
def test_seed_populates_sub_class_with_rankable_groups(db_session) -> None:
    """The Stage 4B reseed must give every instrument a sub_class
    value that places at least 2 instruments per group (groups of 1
    can't be ranked cross-sectionally)."""
    instruments_seed.seed(db_session)

    from sqlalchemy import select

    from macro_trader.db.models.market_data import Instrument

    expected_instruments = {
        "CL", "BZ", "NG", "HO", "RB", "HG", "ALI",
        "GC", "SI", "PL", "ZC", "ZS", "ZW",
    }
    rows = list(
        db_session.scalars(
            select(Instrument).where(Instrument.instrument_id.in_(expected_instruments))
        )
    )
    by_sub_class: dict[str, list[str]] = {}
    for r in rows:
        by_sub_class.setdefault(r.sub_class or "", []).append(r.instrument_id)
    # Every group should have >= 2 members so cross-sectional ranking
    # produces a non-degenerate output.
    for sub_class, members in by_sub_class.items():
        assert len(members) >= 2, (
            f"sub_class {sub_class!r} only has {members} -- "
            "Stage 4B reseed must keep groups multi-member"
        )

    # Spot-check: refined_products contains HO, RB, NG.
    assert set(by_sub_class["refined_products"]) >= {"HO", "RB", "NG"}
    assert set(by_sub_class["crude_oil"]) == {"CL", "BZ"}
    assert set(by_sub_class["grains"]) == {"ZC", "ZS", "ZW"}


@pytest.mark.integration
def test_class_column_changes_rank_outputs(db_session) -> None:
    """Same panel + same lookback, two different class_columns must
    produce at least some rank values that differ — proving the
    grouping is actually being applied to the rank step."""
    instruments_seed.seed(db_session)
    universe = ["CL", "BZ", "NG", "HO", "RB"]  # all in energy / asset_class
    _seed_prices(db_session, universe)

    now = utcnow()
    sig_input = SignalInput(
        instrument_ids=universe,
        as_of=now,
        start=now - timedelta(days=14),
        end=now,
    )

    by_sub_class = CrossSectionalValue(class_column="sub_class").compute(
        sig_input, db_session
    )
    by_asset_class = CrossSectionalValue(class_column="asset_class").compute(
        sig_input, db_session
    )

    # Index outputs by (instrument, value_ts) and compare ranks.
    map_a = {(o.instrument_id, o.value_ts): o.rank for o in by_sub_class}
    map_b = {(o.instrument_id, o.value_ts): o.rank for o in by_asset_class}
    common = sorted(map_a.keys() & map_b.keys())
    if not common:
        pytest.skip("no overlapping outputs to compare (insufficient seed history?)")
    differs = [k for k in common if map_a[k] != map_b[k]]
    assert differs, (
        "switching class_column from sub_class to asset_class produced "
        "identical ranks across every output -- groupings did not change"
    )


@pytest.mark.integration
def test_changing_instrument_sub_class_changes_rank(db_session) -> None:
    """If an instrument moves between sub-classes mid-run, its rank
    should reflect the new peer set."""
    instruments_seed.seed(db_session)
    universe = ["CL", "BZ", "HO", "RB", "NG"]
    _seed_prices(db_session, universe)

    now = utcnow()
    sig_input = SignalInput(
        instrument_ids=universe,
        as_of=now,
        start=now - timedelta(days=14),
        end=now,
    )
    method = CrossSectionalValue(class_column="sub_class")
    before = {
        (o.instrument_id, o.value_ts): o.rank for o in method.compute(sig_input, db_session)
    }

    # Move NG from refined_products into crude_oil to swap its peer set.
    upsert_instrument(
        db_session,
        instrument_id="NG",
        name="Natural Gas",
        asset_class="energy",
        sub_class="crude_oil",
        proxy_ticker="UNG",
        proxy_type="etf",
    )

    after = {
        (o.instrument_id, o.value_ts): o.rank for o in method.compute(sig_input, db_session)
    }

    ng_keys = [k for k in before if k[0] == "NG" and k in after]
    if not ng_keys:
        pytest.skip("no NG outputs to compare")
    differs = [k for k in ng_keys if before[k] != after[k]]
    assert differs, (
        "moving NG to a different sub-class did not change its rank — "
        "the resolver is not reading the live DB row"
    )
