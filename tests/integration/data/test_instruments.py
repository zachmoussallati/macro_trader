"""Integration tests for ``macro_trader.data.instruments``.

Exercises :func:`get_class_groups` against real instrument-master rows
so Phase 1c's switch from hardcoded ``DEFAULT_SUB_CLASS_GROUPS`` to a
DB query is covered end-to-end.
"""

from __future__ import annotations

import pytest

from macro_trader.data.instruments import get_class_groups, upsert_instrument


def _seed_minimal_instruments(session) -> None:
    upsert_instrument(
        session,
        instrument_id="CL",
        name="WTI Crude",
        asset_class="energy",
        sub_class="crude_oil",
    )
    upsert_instrument(
        session,
        instrument_id="BZ",
        name="Brent",
        asset_class="energy",
        sub_class="crude_oil",
    )
    upsert_instrument(
        session,
        instrument_id="GC",
        name="Gold",
        asset_class="precious_metals",
        sub_class="gold",
    )
    upsert_instrument(
        session,
        instrument_id="SI",
        name="Silver",
        asset_class="precious_metals",
        sub_class="silver",
    )


@pytest.mark.integration
def test_get_class_groups_by_asset_class(db_session) -> None:
    _seed_minimal_instruments(db_session)
    groups = get_class_groups(
        db_session, ["CL", "BZ", "GC", "SI"], column="asset_class"
    )
    assert set(groups.keys()) == {"energy", "precious_metals"}
    assert set(groups["energy"]) == {"CL", "BZ"}
    assert set(groups["precious_metals"]) == {"GC", "SI"}


@pytest.mark.integration
def test_get_class_groups_by_sub_class_fragments_finer(db_session) -> None:
    """sub_class is fine-grained — each commodity gets its own group."""
    _seed_minimal_instruments(db_session)
    groups = get_class_groups(
        db_session, ["CL", "BZ", "GC", "SI"], column="sub_class"
    )
    assert set(groups.keys()) == {"crude_oil", "gold", "silver"}
    assert set(groups["crude_oil"]) == {"CL", "BZ"}
    assert groups["gold"] == ["GC"]


@pytest.mark.integration
def test_get_class_groups_drops_unknown_instruments(db_session) -> None:
    _seed_minimal_instruments(db_session)
    groups = get_class_groups(
        db_session, ["CL", "MISSING", "GC"], column="asset_class"
    )
    flat = [iid for ids in groups.values() for iid in ids]
    assert "MISSING" not in flat
    assert "CL" in flat
    assert "GC" in flat


@pytest.mark.integration
def test_get_class_groups_returns_empty_for_empty_input(db_session) -> None:
    assert get_class_groups(db_session, [], column="asset_class") == {}


@pytest.mark.integration
def test_get_class_groups_rejects_unknown_column(db_session) -> None:
    with pytest.raises(ValueError, match="column"):
        get_class_groups(db_session, ["CL"], column="not_a_column")


@pytest.mark.integration
def test_changing_instrument_sub_class_changes_grouping(db_session) -> None:
    """Mutating the row's sub_class flips its group, demonstrating the
    DB-driven nature of the resolver (Stage 4A definition-of-done)."""
    _seed_minimal_instruments(db_session)
    before = get_class_groups(db_session, ["GC"], column="sub_class")
    assert before == {"gold": ["GC"]}

    upsert_instrument(
        session=db_session,
        instrument_id="GC",
        name="Gold",
        asset_class="precious_metals",
        sub_class="reclassified",
    )
    after = get_class_groups(db_session, ["GC"], column="sub_class")
    assert after == {"reclassified": ["GC"]}
