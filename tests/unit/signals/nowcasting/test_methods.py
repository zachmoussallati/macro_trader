"""Nowcasting method tests (metadata + session-required +
release-spec sanity)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from macro_trader.signals.base import SignalInput
from macro_trader.signals.nowcasting.methods import (
    BVARNowcaster,
    OLSARNowcaster,
)
from macro_trader.signals.nowcasting.releases import DEFAULT_RELEASES


@pytest.mark.unit
def test_default_releases_has_six_entries() -> None:
    assert len(DEFAULT_RELEASES) == 6
    ids = {r.release_id for r in DEFAULT_RELEASES}
    assert ids == {
        "NFP", "CPI", "ISM_MFG", "RETAIL_SALES", "GDP", "EIA_PETROLEUM",
    }
    # Every release must point at a FRED-prefixed series id (or
    # EIA: for the petroleum case).
    for r in DEFAULT_RELEASES:
        assert r.target_fred.startswith(("FRED:", "EIA:"))


@pytest.mark.unit
def test_ols_ar_metadata() -> None:
    m = OLSARNowcaster()
    assert m.metadata.method_id == "nowcasting.ols_ar.v1"
    assert m.metadata.component == "nowcasting_signal"


@pytest.mark.unit
def test_bvar_metadata() -> None:
    m = BVARNowcaster()
    assert m.metadata.method_id == "nowcasting.bvar.v1"
    assert m.metadata.component == "nowcasting_signal"
    assert m.USE_BAYESIAN is True


@pytest.mark.unit
def test_nowcasting_methods_require_session() -> None:
    inp = SignalInput(
        instrument_ids=["GC"],
        as_of=datetime(2024, 12, 1, tzinfo=UTC),
        start=datetime(2024, 11, 1, tzinfo=UTC),
        end=datetime(2024, 12, 1, tzinfo=UTC),
    )
    for m in (OLSARNowcaster(), BVARNowcaster()):
        with pytest.raises(ValueError, match="session"):
            m.compute(inp, None)


@pytest.mark.unit
def test_nowcasting_unfit_serializes_empty() -> None:
    for m in (OLSARNowcaster(), BVARNowcaster()):
        assert m.serialize() == b""
        restored = type(m).deserialize(b"")
        assert restored._state is None
