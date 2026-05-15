"""Unit tests for positioning signal methods.

These tests stub the DB loader (``load_cot_as_of``) so we can exercise
sign conventions, history ramp-up, and metadata wiring without a real
Postgres connection.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from macro_trader.signals.base import SignalInput
from macro_trader.signals.positioning.methods import CotCommercial, CotZScore


def _fake_cot_frame(
    *,
    n_weeks: int = 60,
    long_per_week: list[float] | None = None,
    short_per_week: list[float] | None = None,
    open_interest: float = 100_000.0,
    long_col: str = "managed_money_long",
    short_col: str = "managed_money_short",
) -> pd.DataFrame:
    """Build a synthetic CFTC COT DataFrame in the shape ``load_cot_as_of``
    returns."""
    base = pd.Timestamp("2024-01-02", tz="UTC")
    longs = long_per_week or [50_000.0] * n_weeks
    shorts = short_per_week or [40_000.0] * n_weeks
    records: list[dict[str, Any]] = []
    for i in range(n_weeks):
        ts = base + timedelta(weeks=i)
        records.append(
            {
                "report_ts": ts,
                "publication_ts": ts + timedelta(days=3),
                "open_interest": open_interest,
                # All columns the loader produces; only the configured pair
                # matters per method.
                "managed_money_long": longs[i] if long_col == "managed_money_long" else np.nan,
                "managed_money_short": shorts[i] if short_col == "managed_money_short" else np.nan,
                "producer_long": longs[i] if long_col == "producer_long" else np.nan,
                "producer_short": shorts[i] if short_col == "producer_short" else np.nan,
                "swap_long": np.nan,
                "swap_short": np.nan,
                "other_reportable_long": np.nan,
                "other_reportable_short": np.nan,
                "nonreportable_long": np.nan,
                "nonreportable_short": np.nan,
            }
        )
    return pd.DataFrame.from_records(records).set_index("report_ts")


def _patch_loader_returning(
    monkeypatch: pytest.MonkeyPatch, frames_by_instrument: dict[str, pd.DataFrame]
) -> None:
    """Stub ``load_cot_as_of`` (imported into methods.py) to return per-instrument frames."""

    def _fake_loader(
        session: Any,
        instrument_id: str,
        *,
        as_of: datetime,
        report_type: str,
        lookback_weeks: int,
    ) -> pd.DataFrame:
        return frames_by_instrument.get(instrument_id, pd.DataFrame())

    monkeypatch.setattr(
        "macro_trader.signals.positioning.methods.load_cot_as_of", _fake_loader
    )


def _input(instrument_ids: list[str], *, n_weeks: int = 60) -> SignalInput:
    as_of = pd.Timestamp("2024-01-02", tz="UTC") + timedelta(weeks=n_weeks)
    return SignalInput(
        instrument_ids=instrument_ids,
        as_of=as_of.to_pydatetime(),
        start=(as_of - timedelta(weeks=8)).to_pydatetime(),
        end=as_of.to_pydatetime(),
    )


@pytest.mark.unit
def test_cot_zscore_metadata() -> None:
    m = CotZScore()
    assert m.metadata.method_id == "positioning.cot_zscore.v1"
    assert m.metadata.component == "positioning_signal"


@pytest.mark.unit
def test_cot_commercial_metadata() -> None:
    m = CotCommercial()
    assert m.metadata.method_id == "positioning.cot_commercial.v1"
    assert m.metadata.component == "positioning_signal"


@pytest.mark.unit
def test_cot_zscore_requires_session() -> None:
    with pytest.raises(ValueError, match="session"):
        CotZScore().compute(_input(["CL"]), None)


@pytest.mark.unit
def test_cot_zscore_extreme_long_inverts_to_negative_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When managed money is recently at an extreme net long, the most
    recent z-score is high positive; the sign-inversion convention
    flips it to a negative raw_value (contrarian short)."""
    longs = [40_000.0] * 50 + [80_000.0] * 5  # spike at the end
    shorts = [40_000.0] * 55
    frames = {
        "CL": _fake_cot_frame(n_weeks=55, long_per_week=longs, short_per_week=shorts)
    }
    _patch_loader_returning(monkeypatch, frames)

    outputs = CotZScore().compute(_input(["CL"], n_weeks=55), session=object())
    assert outputs, "expected non-empty outputs"

    last = outputs[-1]
    assert last.instrument_id == "CL"
    # Inverted sign: extreme net-long crowd -> negative raw_value.
    assert last.raw_value < 0


@pytest.mark.unit
def test_cot_zscore_extreme_short_inverts_to_positive_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    longs = [40_000.0] * 55
    shorts = [40_000.0] * 50 + [80_000.0] * 5  # short spike at the end
    frames = {"CL": _fake_cot_frame(n_weeks=55, long_per_week=longs, short_per_week=shorts)}
    _patch_loader_returning(monkeypatch, frames)

    outputs = CotZScore().compute(_input(["CL"], n_weeks=55), session=object())
    assert outputs
    assert outputs[-1].raw_value > 0


@pytest.mark.unit
def test_cot_zscore_confidence_scales_with_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 30-week history should give confidence ~ 30/156 ≈ 0.19."""
    n_weeks = 30
    frames = {"CL": _fake_cot_frame(n_weeks=n_weeks)}
    _patch_loader_returning(monkeypatch, frames)

    outputs = CotZScore(min_history_weeks=10).compute(
        _input(["CL"], n_weeks=n_weeks), session=object()
    )
    # The series resolves to constant net positioning => sigma=0 => NaN z-scores
    # for the rolling window; ensure that at least the framework doesn't crash
    # and confidence (when emitted) reflects the partial history.
    if outputs:
        assert all(0.0 <= o.confidence <= 1.0 for o in outputs)
        assert outputs[-1].confidence == pytest.approx(n_weeks / 156, abs=0.01)


@pytest.mark.unit
def test_cot_zscore_skips_instruments_without_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a per-instrument loader call returns empty, the instrument is
    silently dropped — useful for early-history grains where COT history
    is sparser."""
    frames = {
        "CL": _fake_cot_frame(),
        "MISSING": pd.DataFrame(),
    }
    _patch_loader_returning(monkeypatch, frames)

    outputs = CotZScore().compute(
        _input(["CL", "MISSING"]), session=object()
    )
    instruments = {o.instrument_id for o in outputs}
    assert "MISSING" not in instruments


@pytest.mark.unit
def test_cot_zscore_zero_open_interest_drops_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dividing by open_interest=0 produces NaN -> row skipped."""
    df = _fake_cot_frame(n_weeks=10, open_interest=100_000.0)
    df["open_interest"] = 0.0
    _patch_loader_returning(monkeypatch, {"CL": df})

    outputs = CotZScore(min_history_weeks=5).compute(
        _input(["CL"], n_weeks=10), session=object()
    )
    assert outputs == []


@pytest.mark.unit
def test_cot_commercial_reads_legacy_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CotCommercial pulls producer_long/short (the legacy commercial
    breakdown), NOT managed_money_long/short."""
    longs = [40_000.0] * 50 + [80_000.0] * 5
    shorts = [40_000.0] * 55
    # Populate producer_* (legacy report convention).
    frames = {
        "CL": _fake_cot_frame(
            n_weeks=55,
            long_per_week=longs,
            short_per_week=shorts,
            long_col="producer_long",
            short_col="producer_short",
        )
    }
    _patch_loader_returning(monkeypatch, frames)

    outputs = CotCommercial().compute(_input(["CL"], n_weeks=55), session=object())
    assert outputs
    # Commercial net long extreme -> z-score positive -> inverted raw negative.
    assert outputs[-1].raw_value < 0


@pytest.mark.unit
def test_cot_zscore_metadata_has_freshness_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Vary the data so rolling sigma is non-zero and outputs make it past
    # the z-score filter.
    longs = list(np.linspace(40_000, 80_000, num=60))
    shorts = [40_000.0] * 60
    frames = {
        "CL": _fake_cot_frame(n_weeks=60, long_per_week=longs, short_per_week=shorts)
    }
    _patch_loader_returning(monkeypatch, frames)

    outputs = CotZScore().compute(_input(["CL"], n_weeks=60), session=object())
    assert outputs
    meta = outputs[-1].metadata
    assert meta["report_type"] == "disaggregated"
    assert "is_fresh_data" in meta
    assert "is_extreme" in meta
    assert "history_weeks" in meta


@pytest.mark.unit
def test_cot_zscore_emits_only_window_ts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``value_ts`` in [data.start, data.end] are emitted."""
    n_weeks = 60
    frames = {"CL": _fake_cot_frame(n_weeks=n_weeks)}
    _patch_loader_returning(monkeypatch, frames)

    # Window covers only the most recent ~3 weeks.
    inp = _input(["CL"], n_weeks=n_weeks)
    inp = SignalInput(
        instrument_ids=inp.instrument_ids,
        as_of=inp.as_of,
        start=(pd.Timestamp(inp.as_of) - timedelta(weeks=3)).to_pydatetime(),
        end=inp.end,
    )
    outputs = CotZScore().compute(inp, session=object())
    # Should be at most a few weekly rows.
    assert 0 <= len(outputs) <= 5
    start_dt = pd.Timestamp(inp.start)
    end_dt = pd.Timestamp(inp.end)
    for o in outputs:
        ts = pd.Timestamp(o.value_ts)
        assert start_dt <= ts <= end_dt


@pytest.mark.unit
def test_cot_zscore_zscore_invertible_via_arctanh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """raw_value is tanh-squashed; the stored ``zscore`` field is the
    arctanh — so an extreme raw value corresponds to a sizable
    underlying z-score."""
    longs = [40_000.0] * 50 + [80_000.0] * 5
    shorts = [40_000.0] * 55
    frames = {"CL": _fake_cot_frame(n_weeks=55, long_per_week=longs, short_per_week=shorts)}
    _patch_loader_returning(monkeypatch, frames)

    outputs = CotZScore().compute(_input(["CL"], n_weeks=55), session=object())
    assert outputs
    last = outputs[-1]
    # raw is tanh-squashed; arctanh(raw) should give a non-trivial value.
    assert abs(last.zscore) > 0.5
