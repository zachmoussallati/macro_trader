"""BOCPD changepoint-probability conviction dampener.

When the BOCPD method's ``changepoint_probability`` is high, the
regime-conditional weights derived from the trailing 252-day
attribution are *temporarily* invalidated — the system doesn't yet
know what the new regime is, and the old regime's weights may steer
it badly during the transition.

We dampen conviction during transitions with a piecewise-linear
multiplier in ``[floor, 1.0]``:

- ``prob < threshold``: 1.0 (no dampening — the regime is stable).
- ``prob >= threshold``: linearly interpolate from 1.0 at
  ``prob == threshold`` down to ``floor`` at ``prob == 1.0``.

The linear composite applies this multiplier *explicitly* to its
final score; Bayesian + GBM include changepoint probability as a
model feature and learn the appropriate dampening implicitly.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from macro_trader.db.models.regime import RegimeState
from macro_trader.logging_setup import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def transition_multiplier_from_probability(
    changepoint_probability: float | None,
    *,
    threshold: float = 0.5,
    floor: float = 0.5,
) -> float:
    """Pure conversion: changepoint probability -> multiplier in
    ``[floor, 1.0]``.

    Stable when ``changepoint_probability`` is None / NaN / outside
    ``[0, 1]`` (returns 1.0 — fail open rather than zeroing out
    every score on a stray missing value).
    """
    if changepoint_probability is None:
        return 1.0
    p = float(changepoint_probability)
    if p != p:  # NaN check without importing math
        return 1.0
    if p < threshold:
        return 1.0
    if p >= 1.0:
        return float(floor)
    # Linear interpolate.
    return float(1.0 - (p - threshold) / (1.0 - threshold) * (1.0 - floor))


def latest_changepoint_probability(
    session: Session,
    *,
    bocpd_method_id: str,
    as_of: datetime,
) -> float | None:
    """Pull the most-recent ``transition_prob`` for the BOCPD method.

    Returns ``None`` if the BOCPD method has no rows in
    ``regime.regime_states`` (e.g. the asset hasn't run yet) — the
    caller should treat this as "no signal to dampen" and pass it
    straight into :func:`transition_multiplier_from_probability`
    which fail-opens to 1.0.
    """
    row = session.scalar(
        select(RegimeState)
        .where(RegimeState.method_id == bocpd_method_id)
        .where(RegimeState.value_ts <= as_of)
        .order_by(RegimeState.value_ts.desc(), RegimeState.observation_ts.desc())
        .limit(1)
    )
    if row is None or row.transition_prob is None:
        return None
    return float(row.transition_prob)


def transition_multiplier(
    session: Session,
    *,
    as_of: datetime,
    bocpd_method_id: str = "regime.bocpd.v1",
    threshold: float = 0.5,
    floor: float = 0.5,
) -> tuple[float, float | None]:
    """Convenience: returns ``(multiplier, latest_changepoint_prob)``.

    Caller can persist the raw probability alongside the multiplier
    so the dashboard can show "today's dampening = 0.7 because cp =
    0.85".
    """
    prob = latest_changepoint_probability(
        session, bocpd_method_id=bocpd_method_id, as_of=as_of
    )
    return (
        transition_multiplier_from_probability(
            prob, threshold=threshold, floor=floor
        ),
        prob,
    )


__all__ = [
    "latest_changepoint_probability",
    "transition_multiplier",
    "transition_multiplier_from_probability",
]
