"""Weekly + quarterly refit for the fitted composite methods.

- ``composite.linear.v1``: stateless; weights snapshot is the only
  "fitted" piece and is handled by :func:`refit_weights`.
- ``composite.bayesian_hier.v1``: weekly Sunday 06:00 UTC. Fits the
  three-level NIG-style hierarchy from the trailing
  ``lookback_days_for_fit`` of signal + regime + returns history.
- ``composite.gbm.v1``: quarterly (first Sunday of Jan/Apr/Jul/Oct
  at 07:00 UTC). Trains LightGBM on the same multi-year panel.

Both fitted methods persist via the standard
``store_serialized_blob`` helper into
``system.methods_registry.serialized_blob``. The daily runner reads
back via :func:`load_bayesian_state` / :func:`load_gbm_state`.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from sqlalchemy import select

from macro_trader.composite.methods import (
    BayesianHierarchicalComposite,
    _build_gbm_features,
    _lightgbm_available,
    _load_regime_panel,
    _load_signal_panel,
)
from macro_trader.composite.weights import (
    compute_regime_conditional_weights,
    persist_weight_snapshot,
)
from macro_trader.db.models.market_data import DailyBar, Instrument
from macro_trader.logging_setup import get_logger
from macro_trader.methods.registry import load_serialized_blob, store_serialized_blob
from macro_trader.signals.designated import resolve_id
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

_COMPRESS_THRESHOLD_BYTES = 32 * 1024


@dataclass(slots=True)
class CompositeRefitResult:
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


# ----------------------------------------------------------------------
# Returns panel construction
# ----------------------------------------------------------------------
def _build_returns_panel(
    session: Session,
    *,
    instrument_ids: list[str],
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Next-day log returns indexed by (value_ts, instrument_id)."""
    if not instrument_ids:
        return pd.DataFrame(columns=["value_ts", "instrument_id", "next_log_ret"])
    rows = list(
        session.scalars(
            select(DailyBar)
            .where(DailyBar.instrument_id.in_(instrument_ids))
            .where(DailyBar.value_ts >= start - timedelta(days=5))
            .where(DailyBar.value_ts <= end + timedelta(days=5))
        )
    )
    if not rows:
        return pd.DataFrame(columns=["value_ts", "instrument_id", "next_log_ret"])
    df = pd.DataFrame(
        [
            {
                "value_ts": r.value_ts,
                "instrument_id": r.instrument_id,
                "close": float(r.close) if r.close is not None else None,
            }
            for r in rows
        ]
    ).dropna()
    df = df.sort_values(["instrument_id", "value_ts"])
    df["next_log_ret"] = (
        df.groupby("instrument_id")["close"]
        .apply(lambda s: np.log(s.shift(-1) / s))
        .reset_index(level=0, drop=True)
    )
    return df[["value_ts", "instrument_id", "next_log_ret"]].dropna()


# ----------------------------------------------------------------------
# Weights snapshot refit (load-bearing — runs weekly)
# ----------------------------------------------------------------------
def refit_weights(
    session: Session,
    *,
    composite_method_id: str = "composite.linear.v1",
    regime_method_id: str | None = None,
    as_of: datetime | None = None,
) -> int:
    """Compute + persist a new weight snapshot.

    Returns the count of persisted rows. Run weekly Sunday 06:00 UTC
    via Dagster after the attribution job at 05:00.
    """
    as_of = as_of or utcnow()
    regime_method_id = regime_method_id or (
        resolve_id("regime_classifier") or "regime.rules.v1"
    )
    rows = compute_regime_conditional_weights(
        session,
        composite_method_id=composite_method_id,
        regime_method_id=regime_method_id,
        as_of=as_of,
    )
    return persist_weight_snapshot(session, rows)


# ----------------------------------------------------------------------
# Bayesian hierarchical refit (weekly)
# ----------------------------------------------------------------------
def _active_instruments(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
        )
    )


def refit_bayesian_hierarchical(
    session: Session,
    *,
    lookback_days: int = 504,
    as_of: datetime | None = None,
) -> CompositeRefitResult | None:
    now = as_of or utcnow()
    instruments = _active_instruments(session)
    if not instruments:
        log.info("composite.refit.bayesian.no_instruments")
        return None

    designated = [
        m for m in (resolve_id(c) for c in (
            "trend_signal",
            "carry_signal",
            "value_signal",
            "positioning_signal",
            "dislocation_signal",
            "factor_exposure_signal",
            "catalyst_signal",
            "alt_data_signal",
            "nowcasting_signal",
            "vol_surface_signal",
        )) if m is not None
    ]
    if not designated:
        log.info("composite.refit.bayesian.no_signals")
        return None

    regime_method_id = resolve_id("regime_classifier") or "regime.rules.v1"
    start = now - timedelta(days=lookback_days)
    signals = _load_signal_panel(
        session,
        signal_method_ids=designated,
        instrument_ids=instruments,
        start=start,
        end=now,
        as_of=now,
    )
    regime = _load_regime_panel(
        session,
        regime_method_id=regime_method_id,
        start=start,
        end=now,
        as_of=now,
    )
    returns = _build_returns_panel(
        session, instrument_ids=instruments, start=start, end=now
    )
    if signals.empty or regime.empty or returns.empty:
        log.info("composite.refit.bayesian.empty_input")
        return None

    method = BayesianHierarchicalComposite(lookback_days=lookback_days)
    method.fit_from_history(
        signal_panel=signals,
        regime_panel=regime,
        returns_panel=returns,
    )
    if method._state is None:
        log.info("composite.refit.bayesian.no_state")
        return None
    raw_blob = method.serialize()
    if not raw_blob:
        return None
    final_blob, compressed = _maybe_compress(raw_blob)
    store_serialized_blob(session, method.metadata.method_id, final_blob)
    return CompositeRefitResult(
        method_id=method.metadata.method_id,
        blob_size_bytes=len(final_blob),
        fit_rows=int(method._state.get("n_observations", 0)),
        compressed=compressed,
    )


def load_bayesian_state(session: Session) -> BayesianHierarchicalComposite | None:
    blob = _maybe_decompress(
        load_serialized_blob(session, "composite.bayesian_hier.v1")
    )
    return BayesianHierarchicalComposite.deserialize(blob) if blob else None


# ----------------------------------------------------------------------
# GBM refit (quarterly)
# ----------------------------------------------------------------------
def refit_gbm(
    session: Session,
    *,
    lookback_days: int = 1008,
    as_of: datetime | None = None,
) -> CompositeRefitResult | None:
    if not _lightgbm_available():
        log.info("composite.refit.gbm_skipped_no_lightgbm")
        return None

    from macro_trader.composite.methods import GBMComposite

    now = as_of or utcnow()
    instruments = _active_instruments(session)
    if not instruments:
        return None
    designated = [
        m for m in (resolve_id(c) for c in (
            "trend_signal",
            "carry_signal",
            "value_signal",
            "positioning_signal",
            "dislocation_signal",
            "factor_exposure_signal",
            "catalyst_signal",
            "alt_data_signal",
            "nowcasting_signal",
            "vol_surface_signal",
        )) if m is not None
    ]
    if not designated:
        return None

    regime_method_id = resolve_id("regime_classifier") or "regime.rules.v1"
    start = now - timedelta(days=lookback_days)
    signals = _load_signal_panel(
        session,
        signal_method_ids=designated,
        instrument_ids=instruments,
        start=start,
        end=now,
        as_of=now,
    )
    regime = _load_regime_panel(
        session,
        regime_method_id=regime_method_id,
        start=start,
        end=now,
        as_of=now,
    )
    returns = _build_returns_panel(
        session, instrument_ids=instruments, start=start, end=now
    )
    if signals.empty or returns.empty:
        return None

    method = GBMComposite(lookback_days=lookback_days)
    method.fit_from_history(
        signal_panel=signals,
        regime_panel=regime,
        returns_panel=returns,
        instrument_ids=instruments,
    )
    if method._state is None:
        return None
    raw_blob = method.serialize()
    if not raw_blob:
        return None
    final_blob, compressed = _maybe_compress(raw_blob)
    store_serialized_blob(session, method.metadata.method_id, final_blob)
    # _build_gbm_features is imported above for reuse in tests; keep
    # the reference to avoid an unused-import lint.
    _ = _build_gbm_features
    return CompositeRefitResult(
        method_id=method.metadata.method_id,
        blob_size_bytes=len(final_blob),
        fit_rows=int(method._state.get("n_observations", 0)),
        compressed=compressed,
    )


def load_gbm_state(session: Session):  # type: ignore[no-untyped-def]
    if not _lightgbm_available():
        return None
    from macro_trader.composite.methods import GBMComposite

    blob = _maybe_decompress(load_serialized_blob(session, "composite.gbm.v1"))
    return GBMComposite.deserialize(blob) if blob else None


__all__ = [
    "CompositeRefitResult",
    "load_bayesian_state",
    "load_gbm_state",
    "refit_bayesian_hierarchical",
    "refit_gbm",
    "refit_weights",
]
