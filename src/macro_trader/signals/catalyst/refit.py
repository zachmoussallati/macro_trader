"""Weekly refit for the catalyst family.

Estimates per-(instrument, subject) sensitivities over the trailing
``lookback_years`` of historical events and persists them via
``store_serialized_blob``. The daily inference reads back the cached
sensitivities and applies them to upcoming events.
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
from macro_trader.signals.catalyst.methods import (
    CausalCatalyst,
    EventStudyCatalyst,
    _econml_available,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

_COMPRESS_THRESHOLD_BYTES = 32 * 1024


@dataclass(slots=True)
class CatalystRefitResult:
    method_id: str
    blob_size_bytes: int
    n_subjects: int
    n_historicals: int
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
    session: Session, method: EventStudyCatalyst | CausalCatalyst, label: str
) -> CatalystRefitResult | None:
    instruments = _active_instruments(session)
    if not instruments:
        return None
    method.fit_on_session(session, utcnow(), instruments)
    state = method._state
    if state is None:
        log.warning("signals.catalyst.refit.no_state", method=label)
        return None
    raw_blob = method.serialize()
    if not raw_blob:
        return None
    final_blob, compressed = _maybe_compress(raw_blob)
    store_serialized_blob(session, method.metadata.method_id, final_blob)

    return CatalystRefitResult(
        method_id=method.metadata.method_id,
        blob_size_bytes=len(final_blob),
        n_subjects=len(state["sensitivities"]),
        n_historicals=int(state["n_historicals"]),
        compressed=compressed,
    )


def refit_event_study(session: Session) -> CatalystRefitResult | None:
    return _refit_one(session, EventStudyCatalyst(), "event_study")


def refit_causal(session: Session) -> CatalystRefitResult | None:
    if not _econml_available():
        log.info("signals.catalyst.causal.refit_skipped_no_econml")
        return None
    return _refit_one(session, CausalCatalyst(), "causal")


def run_weekly_refit(session: Session) -> list[CatalystRefitResult]:
    out: list[CatalystRefitResult] = []
    for fn, label in [(refit_event_study, "event_study"), (refit_causal, "causal")]:
        try:
            result = fn(session)
        except Exception as exc:
            log.error(
                "signals.catalyst.refit.failed",
                method=label,
                error=str(exc),
                exc_info=True,
            )
            continue
        if result is None:
            continue
        out.append(result)
        log.info(
            "signals.catalyst.refit.complete",
            method=result.method_id,
            blob_size=result.blob_size_bytes,
            subjects=result.n_subjects,
            historicals=result.n_historicals,
        )
    return out


def load_event_study_state(session: Session) -> EventStudyCatalyst | None:
    blob = _maybe_decompress(load_serialized_blob(session, "catalyst.event_study.v1"))
    return EventStudyCatalyst.deserialize(blob) if blob else None


def load_causal_state(session: Session) -> CausalCatalyst | None:
    if not _econml_available():
        return None
    blob = _maybe_decompress(load_serialized_blob(session, "catalyst.causal.v1"))
    return CausalCatalyst.deserialize(blob) if blob else None


__all__ = [
    "CatalystRefitResult",
    "load_causal_state",
    "load_event_study_state",
    "refit_causal",
    "refit_event_study",
    "run_weekly_refit",
]
