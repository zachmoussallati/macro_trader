"""Weekly refit logic for the dislocation models.

Run by the ``dislocation_models_refit`` Dagster asset (Sunday 00:00
UTC). For each method (PCA + DFM):

1. Load the trailing 504-day return panel for all active instruments.
2. Read prior fitted state from
   ``system.methods_registry.serialized_blob`` (if any).
3. Fit on the new window. For PCA, align signs to the prior week's
   components so loadings stay continuous in the dashboard.
4. Serialize and persist the new state via ``store_serialized_blob``.

The daily ``signal_dislocation`` asset later reads the persisted state
via ``deserialize`` + applies it to the day's data.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pandas as pd
from sqlalchemy import select

from macro_trader.data.loaders import load_close_panel
from macro_trader.db.models.market_data import Instrument
from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import (
    load_serialized_blob,
    store_serialized_blob,
)
from macro_trader.signals.dislocation.methods import (
    DynamicFactorModel,
    PCADislocation,
    _compute_returns,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


# Compress when the raw pickle exceeds this size; Postgres TOAST kicks
# in around 2KB-8KB depending on column compression policy. Keep our
# own threshold conservative so the column stays inline when possible.
_COMPRESS_THRESHOLD_BYTES = 32 * 1024


@dataclass(slots=True)
class RefitResult:
    method_id: str
    blob_size_bytes: int
    fit_rows: int
    columns: int
    explained_variance: float
    compressed: bool


def _maybe_compress(blob: bytes) -> tuple[bytes, bool]:
    """Apply zlib compression only when the raw blob is large enough to
    benefit. Returns (final_blob, was_compressed)."""
    if len(blob) < _COMPRESS_THRESHOLD_BYTES:
        return blob, False
    compressed = b"ZLIB" + zlib.compress(blob)
    return compressed, True


def _maybe_decompress(blob: bytes | None) -> bytes | None:
    if blob is None or len(blob) < 4:
        return blob
    if blob[:4] == b"ZLIB":
        return zlib.decompress(blob[4:])
    return blob


def _active_instruments(session: Session) -> list[str]:
    rows = session.scalars(
        select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
    ).all()
    return list(rows)


def _load_panel(
    session: Session, instrument_ids: list[str], lookback_days: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load close panel + log returns for the trailing ``lookback_days``."""
    now = utcnow()
    panel = load_close_panel(
        session,
        instrument_ids,
        start=now - timedelta(days=lookback_days + 60),
        end=now,
        as_of=now,
    )
    returns = _compute_returns(panel) if not panel.empty else panel
    return panel, returns


def refit_pca(session: Session) -> RefitResult | None:
    """Refit PCA on the trailing window, aligning signs to the prior fit.

    Returns ``None`` if there are too few instruments or rows for the
    fit to succeed."""
    method = PCADislocation()
    instruments = _active_instruments(session)
    if not instruments:
        return None
    _, returns = _load_panel(session, instruments, method.lookback_days)
    if returns is None or returns.empty:
        return None

    prior_blob = _maybe_decompress(
        load_serialized_blob(session, method.metadata.method_id)
    )
    method.fit_on_returns(returns)
    if method._state is None:
        log.warning("signals.dislocation.pca.refit_no_state")
        return None

    if prior_blob:
        prior_state = PCADislocation.deserialize(prior_blob)._state
        if prior_state is not None:
            method.align_signs_to(prior_state)

    raw_blob = method.serialize()
    final_blob, compressed = _maybe_compress(raw_blob)
    store_serialized_blob(session, method.metadata.method_id, final_blob)

    state = method._state
    return RefitResult(
        method_id=method.metadata.method_id,
        blob_size_bytes=len(final_blob),
        fit_rows=int(state["fit_rows"]),
        columns=len(state["columns"]),
        explained_variance=float(sum(state["explained_variance_ratio"])),
        compressed=compressed,
    )


def refit_dfm(session: Session) -> RefitResult | None:
    """Refit DFM on the trailing window. Returns ``None`` if the fit
    fails (statsmodels convergence is flaky)."""
    method = DynamicFactorModel()
    instruments = _active_instruments(session)
    if not instruments:
        return None
    _, returns = _load_panel(session, instruments, method.lookback_days)
    if returns is None or returns.empty:
        return None

    method.fit_on_returns(returns)
    if method._state is None:
        log.warning("signals.dislocation.dfm.refit_no_state")
        return None

    raw_blob = method.serialize()
    if not raw_blob:
        return None
    final_blob, compressed = _maybe_compress(raw_blob)
    store_serialized_blob(session, method.metadata.method_id, final_blob)

    state = method._state
    return RefitResult(
        method_id=method.metadata.method_id,
        blob_size_bytes=len(final_blob),
        fit_rows=int(state["fit_rows"]),
        columns=len(state["columns"]),
        explained_variance=float(state["explained_variance"]),
        compressed=compressed,
    )


def run_weekly_refit(session: Session) -> list[RefitResult]:
    """Refit both methods, persist their fitted state. Returns one
    ``RefitResult`` per method that succeeded."""
    out: list[RefitResult] = []
    for fn, label in [(refit_pca, "pca"), (refit_dfm, "dfm")]:
        try:
            result = fn(session)
        except Exception as exc:
            log.error(
                "signals.dislocation.refit.failed", method=label, error=str(exc), exc_info=True
            )
            continue
        if result is None:
            continue
        out.append(result)
        log.info(
            "signals.dislocation.refit.complete",
            method=result.method_id,
            blob_size=result.blob_size_bytes,
            fit_rows=result.fit_rows,
            columns=result.columns,
            explained_variance=result.explained_variance,
            compressed=result.compressed,
        )
    return out


def load_pca_state(session: Session) -> PCADislocation | None:
    """Read the latest persisted PCA state and return a fitted instance."""
    blob = _maybe_decompress(load_serialized_blob(session, "dislocation.pca.v1"))
    if not blob:
        return None
    return PCADislocation.deserialize(blob)


def load_dfm_state(session: Session) -> DynamicFactorModel | None:
    blob = _maybe_decompress(load_serialized_blob(session, "dislocation.dfm.v1"))
    if not blob:
        return None
    return DynamicFactorModel.deserialize(blob)


__all__ = [
    "RefitResult",
    "load_dfm_state",
    "load_pca_state",
    "refit_dfm",
    "refit_pca",
    "run_weekly_refit",
]
