"""Weekly refit logic for the factor exposure family.

Run by the ``factor_exposure_models_refit`` Dagster asset (Sunday
01:00 UTC, staggered after dislocation_models_refit). For each
registered method (OLS, RF, optional Causal Forest):

1. Load the trailing return panel + factor panel.
2. Fit. Persist serialized state via ``store_serialized_blob``.

Daily inference reads the cached state via ``load_*_state``. Unfit
methods fall back to fitting on the daily-run window with a logged
warning.

EconML-gated CausalForest: silently skipped when the optional ``[ml]``
extra isn't installed; logged for visibility.
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
from macro_trader.signals.factor_exposure.factors import build_factor_panel
from macro_trader.signals.factor_exposure.methods import (
    OLSFactorExposure,
    RandomForestFactorExposure,
    _econml_available,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


_COMPRESS_THRESHOLD_BYTES = 32 * 1024


@dataclass(slots=True)
class FactorRefitResult:
    method_id: str
    blob_size_bytes: int
    fit_rows: int
    instruments: int
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


def _load_returns_and_factors(
    session: Session, instruments: list[str], lookback_days: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    import numpy as np

    now = utcnow()
    panel = load_close_panel(
        session,
        instruments,
        start=now - timedelta(days=lookback_days + 90),
        end=now,
        as_of=now,
    )
    if panel.empty:
        return panel, panel
    returns = np.log(panel.replace(0, float("nan"))).diff().dropna(how="all")
    factor_panel = build_factor_panel(
        session, as_of=now, lookback_days=lookback_days + 60
    )
    return returns, factor_panel


def _refit_one(
    session: Session, method: object, label: str
) -> FactorRefitResult | None:
    instruments = _active_instruments(session)
    if not instruments:
        return None
    returns, factor_panel = _load_returns_and_factors(
        session, instruments, getattr(method, "lookback_days", 252)
    )
    if returns.empty or factor_panel.empty:
        return None
    method.fit_on_panels(returns, factor_panel)  # type: ignore[attr-defined]
    state = getattr(method, "_state", None)
    if state is None:
        log.warning("signals.factor_exposure.refit.no_state", method=label)
        return None
    raw_blob = method.serialize()  # type: ignore[attr-defined]
    if not raw_blob:
        return None
    final_blob, compressed = _maybe_compress(raw_blob)
    store_serialized_blob(session, method.metadata.method_id, final_blob)  # type: ignore[attr-defined]

    return FactorRefitResult(
        method_id=method.metadata.method_id,  # type: ignore[attr-defined]
        blob_size_bytes=len(final_blob),
        fit_rows=int(state["fit_rows"]),
        instruments=len(state["per_instrument"]),
        compressed=compressed,
    )


def refit_ols(session: Session) -> FactorRefitResult | None:
    return _refit_one(session, OLSFactorExposure(), "ols")


def refit_rf(session: Session) -> FactorRefitResult | None:
    return _refit_one(session, RandomForestFactorExposure(), "rf")


def refit_causal_forest(session: Session) -> FactorRefitResult | None:
    if not _econml_available():
        log.info("signals.factor_exposure.cf.refit_skipped_no_econml")
        return None
    from macro_trader.signals.factor_exposure.methods import (
        CausalForestFactorExposure,
    )

    return _refit_one(session, CausalForestFactorExposure(), "causal_forest")


def run_weekly_refit(session: Session) -> list[FactorRefitResult]:
    out: list[FactorRefitResult] = []
    for fn, label in [
        (refit_ols, "ols"),
        (refit_rf, "rf"),
        (refit_causal_forest, "causal_forest"),
    ]:
        try:
            result = fn(session)
        except Exception as exc:
            log.error(
                "signals.factor_exposure.refit.failed",
                method=label,
                error=str(exc),
                exc_info=True,
            )
            continue
        if result is None:
            continue
        out.append(result)
        log.info(
            "signals.factor_exposure.refit.complete",
            method=result.method_id,
            blob_size=result.blob_size_bytes,
            fit_rows=result.fit_rows,
            instruments=result.instruments,
            compressed=result.compressed,
        )
    return out


def load_ols_state(session: Session) -> OLSFactorExposure | None:
    blob = _maybe_decompress(load_serialized_blob(session, "factor_exposure.ols.v1"))
    return OLSFactorExposure.deserialize(blob) if blob else None


def load_rf_state(session: Session) -> RandomForestFactorExposure | None:
    blob = _maybe_decompress(load_serialized_blob(session, "factor_exposure.rf.v1"))
    return RandomForestFactorExposure.deserialize(blob) if blob else None


def load_causal_forest_state(session: Session) -> object | None:
    if not _econml_available():
        return None
    from macro_trader.signals.factor_exposure.methods import (
        CausalForestFactorExposure,
    )

    blob = _maybe_decompress(
        load_serialized_blob(session, "factor_exposure.causal_forest.v1")
    )
    return CausalForestFactorExposure.deserialize(blob) if blob else None


__all__ = [
    "FactorRefitResult",
    "load_causal_forest_state",
    "load_ols_state",
    "load_rf_state",
    "refit_causal_forest",
    "refit_ols",
    "refit_rf",
    "run_weekly_refit",
]
