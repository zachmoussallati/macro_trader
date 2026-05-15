"""yfinance ingester unit tests — five scenarios.

Same per-source try/except contract as FRED: yfinance failures on a
single ticker are logged and skipped; the run as a whole succeeds.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from macro_trader.data.ingestion.yfinance_source import YFinanceIngester
from tests.fixtures.http import FakeYFinance, yfinance_bars_df
from tests.unit.data.ingestion.conftest import CallLog


def _scalars_returning(items: list[Any]) -> MagicMock:
    """Build a Mock that mimics ``session.scalars(...)`` yielding items."""
    m = MagicMock(name="ScalarResult")
    m.__iter__ = lambda _self: iter(items)
    return m


def _fake_instrument(instrument_id: str, ticker: str) -> SimpleNamespace:
    return SimpleNamespace(
        instrument_id=instrument_id,
        proxy_ticker=ticker,
        is_active=True,
    )


def _install_fake_yfinance(monkeypatch: pytest.MonkeyPatch, fake: FakeYFinance) -> None:
    monkeypatch.setattr("yfinance.download", fake.download)


@pytest.mark.unit
def test_yfinance_happy_path(
    mock_session_factory: Any,
    mock_session: MagicMock,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instruments = [_fake_instrument("CL", "USO")]
    mock_session.scalars.return_value = _scalars_returning(instruments)

    fake = FakeYFinance(responses={"USO": yfinance_bars_df(n_bars=4)})
    _install_fake_yfinance(monkeypatch, fake)

    ingester = YFinanceIngester(session_factory=mock_session_factory, settings=fake_settings)
    stats = ingester.run()

    assert stats.rows_ingested == 4
    assert fake.calls and fake.calls[0]["ticker"] == "USO"
    assert len(patch_base_helpers.create_lineage) == 1
    assert len(patch_base_helpers.finalize_lineage) == 1
    assert patch_base_helpers.touch_freshness[-1]["success"] is True


@pytest.mark.unit
def test_yfinance_empty_response(
    mock_session_factory: Any,
    mock_session: MagicMock,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instruments = [_fake_instrument("CL", "USO")]
    mock_session.scalars.return_value = _scalars_returning(instruments)

    fake = FakeYFinance(responses={"USO": yfinance_bars_df(empty=True)})
    _install_fake_yfinance(monkeypatch, fake)

    ingester = YFinanceIngester(session_factory=mock_session_factory, settings=fake_settings)
    stats = ingester.run()

    assert stats.rows_ingested == 0
    assert len(patch_base_helpers.record_lineage_failure) == 0
    assert patch_base_helpers.touch_freshness[-1]["success"] is True


@pytest.mark.unit
def test_yfinance_rate_limit_per_ticker(
    mock_session_factory: Any,
    mock_session: MagicMock,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """yfinance raises on 429 -> tenacity retries 3x -> per-ticker
    try/except swallows. Run completes with 0 rows."""
    instruments = [_fake_instrument("CL", "USO")]
    mock_session.scalars.return_value = _scalars_returning(instruments)

    fake = FakeYFinance(responses={"USO": RuntimeError("HTTP 429")})
    _install_fake_yfinance(monkeypatch, fake)

    ingester = YFinanceIngester(session_factory=mock_session_factory, settings=fake_settings)
    stats = ingester.run()

    assert stats.rows_ingested == 0
    # 3 retries x 1 ticker.
    assert len(fake.calls) == 3
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_yfinance_malformed_payload_drops_rows(
    mock_session_factory: Any,
    mock_session: MagicMock,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All-NaN rows are filtered by ``df.dropna(how='all')`` inside
    ``_fetch_one``, leaving zero usable bars."""
    instruments = [_fake_instrument("CL", "USO")]
    mock_session.scalars.return_value = _scalars_returning(instruments)

    fake = FakeYFinance(responses={"USO": yfinance_bars_df(malformed=True)})
    _install_fake_yfinance(monkeypatch, fake)

    ingester = YFinanceIngester(session_factory=mock_session_factory, settings=fake_settings)
    stats = ingester.run()

    assert stats.rows_ingested == 0
    assert len(patch_base_helpers.finalize_lineage) == 1
    assert len(patch_base_helpers.record_lineage_failure) == 0


@pytest.mark.unit
def test_yfinance_retry_exhaustion(
    mock_session_factory: Any,
    mock_session: MagicMock,
    fake_settings: Any,
    patch_base_helpers: CallLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All instruments fail repeatedly. Per-ticker try/except keeps
    run() successful."""
    instruments = [
        _fake_instrument("CL", "USO"),
        _fake_instrument("GC", "GLD"),
    ]
    mock_session.scalars.return_value = _scalars_returning(instruments)

    fake = FakeYFinance(
        responses={
            "USO": ConnectionError("upstream down"),
            "GLD": ConnectionError("upstream down"),
        }
    )
    _install_fake_yfinance(monkeypatch, fake)

    ingester = YFinanceIngester(session_factory=mock_session_factory, settings=fake_settings)
    ingester.run()

    # 3 retries x 2 tickers = 6 calls.
    assert len(fake.calls) == 6
    assert len(patch_base_helpers.record_lineage_failure) == 0
    assert patch_base_helpers.touch_freshness[-1]["success"] is True
