"""SignalOutput → DB row helpers + cross-instrument post-processing.

The cross-instrument step (rank computation, z-score normalisation) is
done outside individual signal methods so they only need to return raw
values + confidence; the framework guarantees consistent ranking +
standardisation across the universe.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.db.models.signals import SignalValue
from macro_trader.signals.base import SignalOutput

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def cross_sectional_rank(
    values: dict[str, float], *, sub_class_groups: dict[str, list[str]] | None = None
) -> dict[str, float]:
    """Return rank in [0, 1] per instrument.

    If ``sub_class_groups`` is provided, rank within each sub-class; else
    rank across the whole universe. Ties get the average rank. With one
    instrument in a group, the rank is 0.5 (centre).
    """
    if not values:
        return {}
    if not sub_class_groups:
        s = pd.Series(values)
        ranked = s.rank(method="average", pct=True)
        return {k: float(v) for k, v in ranked.items()}

    out: dict[str, float] = {}
    for _, members in sub_class_groups.items():
        in_group = {k: values[k] for k in members if k in values}
        if not in_group:
            continue
        if len(in_group) == 1:
            (only_k,) = in_group.keys()
            out[only_k] = 0.5
            continue
        s = pd.Series(in_group)
        ranked = s.rank(method="average", pct=True)
        for k, v in ranked.items():
            out[k] = float(v)
    # Fill anything not covered by sub-class with universe-wide rank.
    missing = {k: v for k, v in values.items() if k not in out}
    if missing:
        m = pd.Series(missing).rank(method="average", pct=True)
        for k, v in m.items():
            out.setdefault(k, float(v))
    return out


def rolling_zscore_of_self(
    series: pd.Series,
    *,
    lookback: int = 252,
    min_periods: int | None = None,
) -> pd.Series:
    """Rolling z-score of a per-time signal series. Used for cross-method
    standardisation rather than the underlying-price z-score that value
    signals compute internally."""
    if series.empty:
        return pd.Series(dtype=float, index=series.index)
    min_p = min_periods if min_periods is not None else max(20, lookback // 4)
    mu = series.rolling(window=lookback, min_periods=min_p).mean()
    sigma = series.rolling(window=lookback, min_periods=min_p).std(ddof=1).replace(0, np.nan)
    return (series - mu) / sigma


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------
def persist_signal_outputs(
    session: Session,
    *,
    signal_id: str,
    outputs: list[SignalOutput],
    lineage_id: uuid.UUID | None,
) -> int:
    """UPSERT outputs into ``signals.signal_values``. Returns rows touched."""
    if not outputs:
        return 0
    payload: list[dict[str, Any]] = []
    for o in outputs:
        payload.append(
            {
                "signal_id": signal_id,
                "instrument_id": o.instrument_id,
                "value_ts": o.value_ts,
                "observation_ts": o.observation_ts,
                "raw_value": _opt_float(o.raw_value),
                "zscore": _opt_float(o.zscore),
                "rank": _opt_float(o.rank),
                "confidence": _opt_float(o.confidence),
                "rolling_sharpe_252": _opt_float(o.rolling_sharpe_252),
                "metadata": dict(o.metadata or {}),
                "lineage_id": lineage_id,
            }
        )
    # Use the underlying Table for the insert so the `metadata` column doesn't
    # collide with SQLAlchemy's `Base.metadata` attribute resolution.
    table = SignalValue.__table__
    stmt = pg_insert(table).values(payload)  # type: ignore[arg-type]
    stmt = stmt.on_conflict_do_update(
        index_elements=["signal_id", "instrument_id", "value_ts", "observation_ts"],
        set_={
            "raw_value": stmt.excluded.raw_value,
            "zscore": stmt.excluded.zscore,
            "rank": stmt.excluded.rank,
            "confidence": stmt.excluded.confidence,
            "rolling_sharpe_252": stmt.excluded.rolling_sharpe_252,
            "metadata": stmt.excluded.metadata,
            "lineage_id": stmt.excluded.lineage_id,
        },
    )
    result = session.execute(stmt)
    rowcount = getattr(result, "rowcount", None)
    if rowcount is not None and rowcount > 0:
        return int(rowcount)
    return len(payload)


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return v


__all__ = [
    "cross_sectional_rank",
    "persist_signal_outputs",
    "rolling_zscore_of_self",
]
