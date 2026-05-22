"""Weekly DCC-GARCH refit.

Ledoit-Wolf is stateless and re-estimated daily by the runner.
DCC-GARCH needs a (potentially expensive) fit and stores its
parameters in ``system.methods_registry.serialized_blob``.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from macro_trader.db.models.market_data import Instrument
from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import load_serialized_blob, store_serialized_blob
from macro_trader.portfolio.covariance.methods import (
    DCCGARCHCovariance,
    _arch_available,
    load_returns_panel,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

_COMPRESS_THRESHOLD_BYTES = 32 * 1024


@dataclass(slots=True)
class CovarianceRefitResult:
    method_id: str
    blob_size_bytes: int
    fit_rows: int
    compressed: bool


def _maybe_compress(blob: bytes) -> tuple[bytes, bool]:
    if len(blob) < _COMPRESS_THRESHOLD_BYTES:
        return blob, False
    return b"ZLIB" + zlib.compress(blob), True


def _maybe_decompress(blob: bytes | None) -> bytes | None:
    if blob is None or len(blob) < 4:
        return blob
    if blob[:4] == b"ZLIB":
        return zlib.decompress(blob[4:])
    return blob


def _active_instruments(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
        )
    )


def refit_dcc_garch(
    session: Session,
    *,
    lookback_days: int = 504,
    as_of: datetime | None = None,
) -> CovarianceRefitResult | None:
    if not _arch_available():
        log.info("covariance.dcc_garch.refit_skipped_no_arch")
        return None

    instruments = _active_instruments(session)
    if not instruments:
        return None
    now = as_of or utcnow()
    rets = load_returns_panel(
        session,
        instrument_ids=instruments,
        as_of=now,
        lookback_days=lookback_days,
    )
    if rets.empty or rets.shape[0] < 60:
        log.info("covariance.dcc_garch.empty_returns_panel")
        return None
    method = DCCGARCHCovariance(lookback_days=lookback_days)
    method.fit_from_history(returns_panel=rets[instruments].dropna(how="any"))
    if method._state is None:
        return None
    raw_blob = method.serialize()
    if not raw_blob:
        return None
    final_blob, compressed = _maybe_compress(raw_blob)
    store_serialized_blob(session, method.metadata.method_id, final_blob)
    return CovarianceRefitResult(
        method_id=method.metadata.method_id,
        blob_size_bytes=len(final_blob),
        fit_rows=int(method._state.get("n_observations", 0)),
        compressed=compressed,
    )


def load_dcc_state(session: Session) -> DCCGARCHCovariance | None:
    if not _arch_available():
        return None
    blob = _maybe_decompress(load_serialized_blob(session, "covariance.dcc_garch.v1"))
    if blob is None or blob == b"":
        return None
    return DCCGARCHCovariance.deserialize(blob)


__all__ = [
    "CovarianceRefitResult",
    "load_dcc_state",
    "refit_dcc_garch",
]
