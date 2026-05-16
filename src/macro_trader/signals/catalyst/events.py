"""Event aggregation helpers for the catalyst signal family.

Empirical event-study sensitivity per (instrument, event_subject) is
estimated from historical events whose ``event_ts`` falls in
[as_of - lookback_years, as_of). The forward score combines upcoming
events in [as_of, as_of + forward_window_days) weighted by linear
time-decay and the per-event sensitivity.

Helper outputs:

- :func:`historical_event_returns` returns a list of (event_ts, log_return)
  pairs for each (instrument, event_subject) — the raw input to
  ``estimate_sensitivities``.
- :func:`estimate_sensitivities` computes sensitivity per
  (instrument, subject) as ``mean(|return_in_window|) -
  baseline_volatility``. Subjects with fewer than ``min_events`` are
  silently skipped (low-confidence; downstream marks them).
- :func:`time_decay_weight` linear by default with proximity to event.
- :func:`upcoming_score` aggregates per-instrument forward scores.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd

from macro_trader.calendar.api import events_in_window
from macro_trader.data.loaders import load_close_series
from macro_trader.logging_setup import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


DEFAULT_EVENT_KINDS: tuple[str, ...] = ("data_release", "central_bank", "supply_event")
DEFAULT_IMPORTANCE: tuple[str, ...] = ("medium", "high")


@dataclass(slots=True, frozen=True)
class HistoricalReturn:
    instrument_id: str
    subject: str
    event_ts: datetime
    log_return: float


@dataclass(slots=True, frozen=True)
class EventSensitivity:
    instrument_id: str
    subject: str
    n_events: int
    mean_abs_return: float
    baseline_vol: float
    sensitivity: float  # mean_abs_return - baseline_vol


def _log_return_in_window(
    series: pd.Series, event_ts: datetime, window_days: tuple[int, int]
) -> float | None:
    """Log-return between (event_ts + window[0]) and (event_ts + window[1])."""
    if series.empty:
        return None
    lo = event_ts + timedelta(days=window_days[0])
    hi = event_ts + timedelta(days=window_days[1])
    sub = series.loc[
        (series.index >= pd.Timestamp(lo, tz="UTC"))
        & (series.index <= pd.Timestamp(hi, tz="UTC"))
    ].dropna()
    if len(sub) < 2:
        return None
    return float(np.log(sub.iloc[-1] / sub.iloc[0]))


def historical_event_returns(
    session: Session,
    *,
    instrument_ids: list[str],
    as_of: datetime,
    lookback_years: int = 5,
    event_window: tuple[int, int] = (-1, 1),
    kinds: tuple[str, ...] = DEFAULT_EVENT_KINDS,
    importance: tuple[str, ...] = DEFAULT_IMPORTANCE,
) -> list[HistoricalReturn]:
    """Pull historical events affecting any of ``instrument_ids`` and
    compute the log return for each over ``event_window`` days."""
    start = as_of - timedelta(days=int(lookback_years * 365))
    events = events_in_window(
        session,
        start=start,
        end=as_of,
        instruments=instrument_ids,
        importance=list(importance),
        kinds=list(kinds),
    )
    if not events:
        return []

    # Cache one close-series load per instrument across the whole
    # lookback so we don't hammer the DB per event.
    series_by_inst: dict[str, pd.Series] = {}
    for inst in instrument_ids:
        series_by_inst[inst] = load_close_series(
            session, inst, start=start, end=as_of, as_of=as_of
        )

    out: list[HistoricalReturn] = []
    for event in events:
        for inst in event.affected_instruments:
            if inst not in series_by_inst:
                continue
            r = _log_return_in_window(series_by_inst[inst], event.event_ts, event_window)
            if r is None:
                continue
            out.append(
                HistoricalReturn(
                    instrument_id=inst,
                    subject=event.subject,
                    event_ts=event.event_ts,
                    log_return=r,
                )
            )
    return out


def _baseline_vol(series: pd.Series, window: int = 60) -> float:
    """Recent realised vol (std of daily log-returns over ``window``)."""
    if series.empty:
        return 0.0
    rets = np.log(series.dropna()).diff().dropna()
    if rets.empty:
        return 0.0
    return float(rets.tail(window).std(ddof=1))


def estimate_sensitivities(
    historicals: list[HistoricalReturn],
    *,
    series_by_inst: dict[str, pd.Series],
    min_events: int = 5,
) -> list[EventSensitivity]:
    """Per (instrument, subject), compute sensitivity =
    ``mean(|return|) - baseline_vol``.

    Subjects with fewer than ``min_events`` historical instances are
    dropped — low-confidence sensitivity estimates lead to noisy
    forward scores. The dropped pairs are tracked in metadata
    downstream so the dashboard can show coverage gaps.
    """
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for h in historicals:
        grouped[(h.instrument_id, h.subject)].append(h.log_return)

    out: list[EventSensitivity] = []
    for (inst, subj), rets in grouped.items():
        if len(rets) < min_events:
            continue
        mean_abs = float(np.mean(np.abs(rets)))
        vol = _baseline_vol(series_by_inst.get(inst, pd.Series(dtype=float)))
        out.append(
            EventSensitivity(
                instrument_id=inst,
                subject=subj,
                n_events=len(rets),
                mean_abs_return=mean_abs,
                baseline_vol=vol,
                sensitivity=mean_abs - vol,
            )
        )
    return out


def time_decay_weight(
    days_to_event: float,
    forward_window_days: int = 10,
    decay: Literal["linear", "exponential"] = "linear",
) -> float:
    """Weight an upcoming event by proximity. Linear: weight = 1 -
    days_to_event / forward_window_days. Exponential: half-life =
    forward_window_days / 2.
    """
    if days_to_event < 0 or days_to_event > forward_window_days:
        return 0.0
    if decay == "linear":
        return max(0.0, 1.0 - days_to_event / float(forward_window_days))
    if decay == "exponential":
        half_life = forward_window_days / 2.0
        return float(0.5 ** (days_to_event / half_life)) if half_life > 0 else 0.0
    raise ValueError(f"unknown decay strategy {decay!r}")


def upcoming_score(
    session: Session,
    *,
    instrument_ids: list[str],
    as_of: datetime,
    sensitivities: list[EventSensitivity],
    forward_window_days: int = 10,
    decay: Literal["linear", "exponential"] = "linear",
    kinds: tuple[str, ...] = DEFAULT_EVENT_KINDS,
    importance: tuple[str, ...] = DEFAULT_IMPORTANCE,
) -> dict[str, float]:
    """Per-instrument forward score = sum_{events ahead}
    (sensitivity * time_decay_weight). Magnitude indicates pending
    catalyst risk; sign defaults to zero (positive vs negative reaction
    direction is regime-conditional, deferred to Stage 6).
    """
    end = as_of + timedelta(days=forward_window_days)
    upcoming = events_in_window(
        session,
        start=as_of,
        end=end,
        instruments=instrument_ids,
        importance=list(importance),
        kinds=list(kinds),
    )
    if not upcoming:
        return dict.fromkeys(instrument_ids, 0.0)

    sensitivity_lookup = {(s.instrument_id, s.subject): s.sensitivity for s in sensitivities}

    out: dict[str, float] = dict.fromkeys(instrument_ids, 0.0)
    for event in upcoming:
        days_ahead = (event.event_ts - as_of).total_seconds() / 86400.0
        weight = time_decay_weight(days_ahead, forward_window_days, decay=decay)
        if weight <= 0:
            continue
        for inst in event.affected_instruments:
            sens = sensitivity_lookup.get((inst, event.subject))
            if sens is None:
                continue
            out[inst] = out.get(inst, 0.0) + weight * sens
    return out


__all__ = [
    "DEFAULT_EVENT_KINDS",
    "DEFAULT_IMPORTANCE",
    "EventSensitivity",
    "HistoricalReturn",
    "estimate_sensitivities",
    "historical_event_returns",
    "time_decay_weight",
    "upcoming_score",
]
