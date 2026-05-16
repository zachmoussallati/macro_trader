"""Weekly refit for nowcasting.

Same shape as factor_exposure / catalyst: fit each method on the
trailing release history and persist via
``system.methods_registry.serialized_blob``. The daily inference
pulls cached state via ``load_*_state``.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from macro_trader.db.models.market_data import Instrument
from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import (
    load_serialized_blob,
    store_serialized_blob,
)
from macro_trader.signals.nowcasting.methods import (
    BVARNowcaster,
    OLSARNowcaster,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

_COMPRESS_THRESHOLD_BYTES = 32 * 1024


@dataclass(slots=True)
class NowcastingRefitResult:
    method_id: str
    blob_size_bytes: int
    n_releases_fitted: int
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


def _refit_one(
    session: Session, method: OLSARNowcaster | BVARNowcaster
) -> NowcastingRefitResult | None:
    instruments = _active_instruments(session)
    method.fit_on_session(session, utcnow(), instruments)
    if method._state is None:
        log.warning("signals.nowcasting.refit_no_state", method_id=method.metadata.method_id)
        return None
    raw_blob = method.serialize()
    if not raw_blob:
        return None
    final_blob, compressed = _maybe_compress(raw_blob)
    store_serialized_blob(session, method.metadata.method_id, final_blob)
    return NowcastingRefitResult(
        method_id=method.metadata.method_id,
        blob_size_bytes=len(final_blob),
        n_releases_fitted=len(method._state["per_release"]),
        compressed=compressed,
    )


def refit_ols_ar(session: Session) -> NowcastingRefitResult | None:
    return _refit_one(session, OLSARNowcaster())


def refit_bvar(session: Session) -> NowcastingRefitResult | None:
    return _refit_one(session, BVARNowcaster())


def run_weekly_refit(session: Session) -> list[NowcastingRefitResult]:
    out: list[NowcastingRefitResult] = []
    for fn, label in [(refit_ols_ar, "ols_ar"), (refit_bvar, "bvar")]:
        try:
            result = fn(session)
        except Exception as exc:
            log.error(
                "signals.nowcasting.refit_failed",
                method=label,
                error=str(exc),
                exc_info=True,
            )
            continue
        if result is None:
            continue
        out.append(result)
        log.info(
            "signals.nowcasting.refit_complete",
            method=result.method_id,
            blob_size=result.blob_size_bytes,
            releases=result.n_releases_fitted,
        )
    return out


def load_ols_ar_state(session: Session) -> OLSARNowcaster | None:
    blob = _maybe_decompress(load_serialized_blob(session, "nowcasting.ols_ar.v1"))
    return OLSARNowcaster.deserialize(blob) if blob else None


def load_bvar_state(session: Session) -> BVARNowcaster | None:
    blob = _maybe_decompress(load_serialized_blob(session, "nowcasting.bvar.v1"))
    return BVARNowcaster.deserialize(blob) if blob else None


__all__ = [
    "NowcastingRefitResult",
    "load_bvar_state",
    "load_ols_ar_state",
    "refit_bvar",
    "refit_ols_ar",
    "run_weekly_refit",
]
