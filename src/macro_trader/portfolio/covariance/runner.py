"""Daily covariance runner.

Runs every registered covariance method on the active-instrument
universe; persists one ``CovarianceEstimate`` row per (method,
as_of, instrument_a, instrument_b) pair plus a
``VolatilityEstimate`` row per instrument.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from macro_trader.db.models.market_data import Instrument
from macro_trader.db.models.portfolio import CovarianceEstimate, VolatilityEstimate
from macro_trader.db.models.system import HeartbeatRow
from macro_trader.logging_setup import get_logger
from macro_trader.portfolio.covariance.methods import (
    CovarianceInput,
    CovarianceMethod,
    CovarianceOutput,
    DCCGARCHCovariance,
    LedoitWolfCovariance,
    _arch_available,
)
from macro_trader.utils.dates import utcnow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def default_covariance_methods(
    session: Session | None = None,
) -> list[CovarianceMethod]:
    """Build the daily method list. Hydrates DCC state when available."""
    from macro_trader.portfolio.covariance.refit import load_dcc_state

    methods: list[CovarianceMethod] = [LedoitWolfCovariance()]
    if _arch_available():
        if session is not None:
            existing = load_dcc_state(session)
            if existing is not None:
                methods.append(existing)
            else:
                methods.append(DCCGARCHCovariance())
        else:
            methods.append(DCCGARCHCovariance())
    return methods


def _active_instruments(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(Instrument.instrument_id).where(Instrument.is_active.is_(True))
        )
    )


def persist_covariance_output(
    session: Session, method_id: str, output: CovarianceOutput
) -> int:
    """Persist the (cov, vol) pair for one method+as_of."""
    if output is None:
        return 0
    cov_rows = []
    n = len(output.instrument_ids)
    for i in range(n):
        for j in range(n):
            cov_rows.append(
                {
                    "method_id": method_id,
                    "as_of": output.as_of,
                    "instrument_a": output.instrument_ids[i],
                    "instrument_b": output.instrument_ids[j],
                    "covariance": float(output.covariance_matrix[i, j]),
                    "correlation": float(output.correlation_matrix[i, j]),
                    "lookback_days": int(output.lookback_days),
                    "cov_metadata": {},
                }
            )
    stmt = pg_insert(CovarianceEstimate).values(cov_rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["method_id", "as_of", "instrument_a", "instrument_b"],
        set_={
            "covariance": stmt.excluded.covariance,
            "correlation": stmt.excluded.correlation,
            "lookback_days": stmt.excluded.lookback_days,
        },
    )
    session.execute(stmt)

    vol_rows = [
        {
            "method_id": method_id,
            "as_of": output.as_of,
            "instrument_id": output.instrument_ids[i],
            "volatility": float(output.annualised_vols[i]),
            "lookback_days": int(output.lookback_days),
            "vol_metadata": output.metadata,
        }
        for i in range(n)
    ]
    stmt2 = pg_insert(VolatilityEstimate).values(vol_rows)
    stmt2 = stmt2.on_conflict_do_update(
        index_elements=["method_id", "as_of", "instrument_id"],
        set_={
            "volatility": stmt2.excluded.volatility,
            "lookback_days": stmt2.excluded.lookback_days,
            "vol_metadata": stmt2.excluded.vol_metadata,
        },
    )
    session.execute(stmt2)
    session.flush()
    return len(cov_rows)


def run_daily_covariance(
    session: Session,
    *,
    instruments: list[str] | None = None,
    methods: Iterable[CovarianceMethod] | None = None,
    as_of: datetime | None = None,
) -> dict[str, int]:
    instruments = instruments or _active_instruments(session)
    now = as_of or utcnow()
    written: dict[str, int] = {}
    method_list = (
        list(methods)
        if methods is not None
        else default_covariance_methods(session)
    )
    for method in method_list:
        inp = CovarianceInput(
            instrument_ids=instruments,
            as_of=now,
            lookback_days=method.lookback_days,
        )
        out = method.compute(inp, session)
        if out is None:
            written[method.metadata.method_id] = 0
            continue
        written[method.metadata.method_id] = persist_covariance_output(
            session, method.metadata.method_id, out
        )

    session.add(
        HeartbeatRow(
            timestamp=now,
            source="portfolio.covariance",
            meta={"rows_written": written},
        )
    )
    session.flush()
    log.info("portfolio.covariance.daily_run.complete", written=written)
    return written


__all__ = [
    "default_covariance_methods",
    "persist_covariance_output",
    "run_daily_covariance",
]
