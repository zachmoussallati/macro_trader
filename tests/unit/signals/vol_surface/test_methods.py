"""Vol-surface method metadata + session-required tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from macro_trader.signals.base import SignalInput
from macro_trader.signals.vol_surface.methods import (
    VOL_SURFACE_UNIVERSE,
    RawVolSurface,
    SVIVolSurface,
)


@pytest.mark.unit
def test_universe_has_six_etfs() -> None:
    assert VOL_SURFACE_UNIVERSE == ("GLD", "SLV", "USO", "UNG", "DBA", "SPY")


@pytest.mark.unit
def test_raw_metadata() -> None:
    m = RawVolSurface()
    assert m.metadata.method_id == "vol_surface.raw.v1"
    assert m.metadata.component == "vol_surface_signal"


@pytest.mark.unit
def test_svi_metadata() -> None:
    m = SVIVolSurface()
    assert m.metadata.method_id == "vol_surface.svi.v1"
    assert m.metadata.component == "vol_surface_signal"
    assert m.USE_SVI is True


@pytest.mark.unit
def test_vol_surface_methods_require_session() -> None:
    inp = SignalInput(
        instrument_ids=["GLD"],
        as_of=datetime(2024, 12, 1, tzinfo=UTC),
        start=datetime(2024, 12, 1, tzinfo=UTC),
        end=datetime(2024, 12, 2, tzinfo=UTC),
    )
    for m in (RawVolSurface(), SVIVolSurface()):
        with pytest.raises(ValueError, match="session"):
            m.compute(inp, None)


@pytest.mark.unit
def test_vol_surface_stateless_serialize_is_empty() -> None:
    for m in (RawVolSurface(), SVIVolSurface()):
        assert m.serialize() == b""
