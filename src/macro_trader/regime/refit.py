"""Weekly + quarterly refit for the fitted regime methods.

- GMM: weekly Sunday 04:00 UTC.
- HMM: quarterly (caller decides; this module's run_weekly_refit
  re-fits HMM on every call when ``include_hmm=True`` is passed, so
  the Dagster schedule can wire it differently).
- MS-VAR: same — caller decides cadence via flags.
- Rules + BOCPD: stateless, no refit.

Each refit serialises fitted state via the standard
``store_serialized_blob`` helper into
``system.methods_registry.serialized_blob``. Daily runner reads
back via the matching ``load_*_state`` helpers.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import (
    load_serialized_blob,
    store_serialized_blob,
)
from macro_trader.regime.features import build_regime_features
from macro_trader.regime.methods import (
    GMMRegimeClassifier,
    HMMRegimeClassifier,
    MSVARRegimeClassifier,
    _hmmlearn_available,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

_COMPRESS_THRESHOLD_BYTES = 32 * 1024


@dataclass(slots=True)
class RegimeRefitResult:
    method_id: str
    blob_size_bytes: int
    fit_rows: int
    n_features: int
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


def _refit_one(
    session: Session,
    method: GMMRegimeClassifier | HMMRegimeClassifier | MSVARRegimeClassifier,
    label: str,
) -> RegimeRefitResult | None:
    now = utcnow()
    panel = build_regime_features(
        session, as_of=now, lookback_days=method.lookback_days
    )
    if panel.empty:
        return None

    # Carry prior state through for centroid-anchored labelling.
    blob_prior = _maybe_decompress(load_serialized_blob(session, method.metadata.method_id))
    if blob_prior:
        try:
            restored = type(method).deserialize(blob_prior)
            method._state = restored._state
        except Exception as exc:  # pragma: no cover - pickle drift
            log.warning(
                "regime.refit.deserialize_failed",
                method=label,
                error=str(exc),
            )

    method.fit_on_panel(panel)
    if method._state is None:
        log.warning("regime.refit.no_state", method=label)
        return None

    raw_blob = method.serialize()
    if not raw_blob:
        return None
    final_blob, compressed = _maybe_compress(raw_blob)
    store_serialized_blob(session, method.metadata.method_id, final_blob)

    state = method._state
    return RegimeRefitResult(
        method_id=method.metadata.method_id,
        blob_size_bytes=len(final_blob),
        fit_rows=int(panel.dropna(how="any").shape[0]),
        n_features=len(state.get("feature_columns", [])),
        compressed=compressed,
    )


def refit_gmm(session: Session) -> RegimeRefitResult | None:
    return _refit_one(session, GMMRegimeClassifier(), "gmm")


def refit_hmm(session: Session) -> RegimeRefitResult | None:
    if not _hmmlearn_available():
        log.info("regime.hmm.refit_skipped_no_hmmlearn")
        return None
    return _refit_one(session, HMMRegimeClassifier(), "hmm")


def refit_msvar(session: Session) -> RegimeRefitResult | None:
    try:
        method = MSVARRegimeClassifier()
    except Exception as exc:  # pragma: no cover - statsmodels env
        log.warning("regime.msvar.refit_skipped", error=str(exc))
        return None
    return _refit_one(session, method, "msvar")


def run_weekly_refit(
    session: Session,
    *,
    include_quarterly: bool = False,
) -> list[RegimeRefitResult]:
    """Refit GMM unconditionally; HMM + MS-VAR only when
    ``include_quarterly=True`` (Dagster wires quarterly schedules
    via separate jobs)."""
    out: list[RegimeRefitResult] = []
    for fn, label, quarterly in (
        (refit_gmm, "gmm", False),
        (refit_hmm, "hmm", True),
        (refit_msvar, "msvar", True),
    ):
        if quarterly and not include_quarterly:
            continue
        try:
            result = fn(session)
        except Exception as exc:
            log.error(
                "regime.refit.failed", method=label, error=str(exc), exc_info=True
            )
            continue
        if result is None:
            continue
        out.append(result)
        log.info(
            "regime.refit.complete",
            method=result.method_id,
            blob_size=result.blob_size_bytes,
            fit_rows=result.fit_rows,
            n_features=result.n_features,
        )
    return out


def load_gmm_state(session: Session) -> GMMRegimeClassifier | None:
    blob = _maybe_decompress(load_serialized_blob(session, "regime.gmm.v1"))
    return GMMRegimeClassifier.deserialize(blob) if blob else None


def load_hmm_state(session: Session) -> HMMRegimeClassifier | None:
    if not _hmmlearn_available():
        return None
    blob = _maybe_decompress(load_serialized_blob(session, "regime.hmm.v1"))
    return HMMRegimeClassifier.deserialize(blob) if blob else None


def load_msvar_state(session: Session) -> MSVARRegimeClassifier | None:
    blob = _maybe_decompress(load_serialized_blob(session, "regime.msvar.v1"))
    return MSVARRegimeClassifier.deserialize(blob) if blob else None


__all__ = [
    "RegimeRefitResult",
    "load_gmm_state",
    "load_hmm_state",
    "load_msvar_state",
    "refit_gmm",
    "refit_hmm",
    "refit_msvar",
    "run_weekly_refit",
]
