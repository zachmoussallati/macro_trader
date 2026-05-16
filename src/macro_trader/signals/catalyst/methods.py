"""Catalyst sensitivity methods.

Two methods:

- ``catalyst.event_study.v1`` (BASELINE): empirical event-study
  sensitivity per (instrument, subject), applied to upcoming events
  with linear time-decay.
- ``catalyst.causal.v1`` (SHADOW): EconML CausalForestDML for
  conditional sensitivities. Gated on the optional ``[ml]`` extra.

Both methods follow the same lifecycle as the dislocation / factor
exposure families:

- ``fit_on_session(session, as_of, instruments)`` populates ``_state``
  with per-(instrument, subject) sensitivities.
- ``compute(data, session)`` reads cached state if present and falls
  back to fitting on the daily window.
- ``serialize`` / ``deserialize`` round-trip via pickle so the weekly
  refit asset can stash fitted state in
  ``system.methods_registry.serialized_blob``.
"""

from __future__ import annotations

import pickle
from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from macro_trader.data.loaders import load_close_series
from macro_trader.logging_setup import get_logger
from macro_trader.methods.base import MethodMetadata
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.signals.catalyst.events import (
    DEFAULT_EVENT_KINDS,
    DEFAULT_IMPORTANCE,
    EventSensitivity,
    estimate_sensitivities,
    historical_event_returns,
    upcoming_score,
)
from macro_trader.signals.output import cross_sectional_rank

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)


def _econml_available() -> bool:
    try:
        import econml.dml  # noqa: F401
    except Exception:
        return False
    return True


# ----------------------------------------------------------------------
# Baseline: event-study
# ----------------------------------------------------------------------
class EventStudyCatalyst(SignalMethod):
    """Event-study estimation of catalyst sensitivities.

    For each (instrument, event_subject) pair, estimate sensitivity =
    mean(|return_in_window|) - baseline_volatility over historical
    events. Daily signal = sum(sensitivity * time_decay_weight) over
    upcoming events in the next ``forward_window_days``.
    """

    metadata = MethodMetadata(
        method_id="catalyst.event_study.v1",
        component="catalyst_signal",
        name="Event-Study Catalyst Sensitivity",
        version="1.0.0",
        description=(
            "Event-study abnormal returns around catalysts; time-decayed "
            "forward score over upcoming events."
        ),
        references=["MacKinlay (1997) Event Studies in Economics and Finance"],
    )

    def __init__(
        self,
        *,
        event_window: tuple[int, int] = (-1, 1),
        lookback_years: int = 5,
        forward_window_days: int = 10,
        time_decay: str = "linear",
        min_events_for_estimate: int = 5,
        kinds: tuple[str, ...] = DEFAULT_EVENT_KINDS,
        importance: tuple[str, ...] = DEFAULT_IMPORTANCE,
    ) -> None:
        self.event_window = event_window
        self.lookback_years = int(lookback_years)
        self.forward_window_days = int(forward_window_days)
        self.time_decay = time_decay
        self.min_events_for_estimate = int(min_events_for_estimate)
        self.kinds = kinds
        self.importance = importance
        self._state: dict[str, Any] | None = None

    # --- fit / serialize -----------------------------------------------
    def fit_on_session(
        self, session: Session, as_of: object, instrument_ids: list[str]
    ) -> None:
        """Estimate sensitivities for each (instrument, subject) pair
        using ``lookback_years`` of historical events."""
        from datetime import datetime as _dt

        as_of_dt = as_of if isinstance(as_of, _dt) else _dt.fromisoformat(str(as_of))

        historicals = historical_event_returns(
            session,
            instrument_ids=instrument_ids,
            as_of=as_of_dt,
            lookback_years=self.lookback_years,
            event_window=self.event_window,
            kinds=self.kinds,
            importance=self.importance,
        )
        if not historicals:
            log.info("signals.catalyst.event_study.no_historicals")
            self._state = None
            return

        # One close-series load per instrument, used for baseline-vol.
        series_by_inst: dict[str, pd.Series] = {}
        for inst in instrument_ids:
            series_by_inst[inst] = load_close_series(
                session,
                inst,
                start=as_of_dt - timedelta(days=180),
                end=as_of_dt,
                as_of=as_of_dt,
            )

        sensitivities = estimate_sensitivities(
            historicals,
            series_by_inst=series_by_inst,
            min_events=self.min_events_for_estimate,
        )
        if not sensitivities:
            self._state = None
            return

        self._state = {
            "sensitivities": [asdict(s) for s in sensitivities],
            "fit_as_of": as_of_dt.isoformat(),
            "n_historicals": len(historicals),
            "instruments": list(instrument_ids),
        }

    def serialize(self) -> bytes:
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL) if self._state else b""

    @classmethod
    def deserialize(cls, blob: bytes) -> EventStudyCatalyst:
        m = cls()
        if blob:
            m._state = pickle.loads(blob)
        return m

    def _sensitivities(self) -> list[EventSensitivity]:
        if self._state is None:
            return []
        return [EventSensitivity(**s) for s in self._state["sensitivities"]]

    # --- compute -------------------------------------------------------
    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("EventStudyCatalyst requires a DB session")
        if self._state is None:
            log.info("signals.catalyst.event_study.fallback_fit")
            self.fit_on_session(session, data.as_of, list(data.instrument_ids))
            if self._state is None:
                return []

        sensitivities = self._sensitivities()
        scores = upcoming_score(
            session,
            instrument_ids=list(data.instrument_ids),
            as_of=data.as_of,
            sensitivities=sensitivities,
            forward_window_days=self.forward_window_days,
            decay="linear" if self.time_decay == "linear" else "exponential",
            kinds=self.kinds,
            importance=self.importance,
        )
        if not scores:
            return []

        # Confidence per instrument: fraction of subjects we have
        # sensitivity for vs total subjects encountered for that
        # instrument in the historical window. Capped at 1.0.
        subject_counts: dict[str, int] = {}
        for s in sensitivities:
            subject_counts[s.instrument_id] = subject_counts.get(s.instrument_id, 0) + 1

        ranks = cross_sectional_rank({k: abs(v) for k, v in scores.items() if v != 0.0})
        outputs: list[SignalOutput] = []
        for inst in data.instrument_ids:
            raw_score = float(scores.get(inst, 0.0))
            n_subjects = subject_counts.get(inst, 0)
            confidence = max(0.05, min(1.0, n_subjects / 5.0))
            outputs.append(
                SignalOutput(
                    instrument_id=inst,
                    value_ts=data.as_of,
                    observation_ts=data.as_of,
                    raw_value=float(np.tanh(raw_score)),
                    zscore=float(raw_score),
                    rank=float(ranks.get(inst, 0.5)),
                    confidence=float(confidence),
                    rolling_sharpe_252=None,
                    metadata={
                        "method_id": self.metadata.method_id,
                        "n_subjects": n_subjects,
                        "forward_window_days": self.forward_window_days,
                    },
                )
            )
        return outputs


# ----------------------------------------------------------------------
# Causal Forest enhancement (gated on EconML)
# ----------------------------------------------------------------------
class CausalCatalyst(SignalMethod):
    """Causal-inference catalyst sensitivity (CausalForestDML).

    Same workflow shape as the baseline but with CATE estimates
    replacing the unconditional mean(|return|). Gated on the
    ``[ml]`` extra; constructor raises ``RuntimeError`` if EconML is
    not installed.

    Stage 4B implements only the metadata + interface; full
    CausalForestDML training over the historical event panel is
    deferred to a Stage 4C / 9 pass once we have enough real data
    for CATE to be stable. ``compute()`` falls back to the
    event-study computation as a placeholder so the daily run still
    produces a row per instrument while the Causal Forest stays
    SHADOW-status.
    """

    metadata = MethodMetadata(
        method_id="catalyst.causal.v1",
        component="catalyst_signal",
        name="Causal Inference Catalyst Sensitivity",
        version="1.0.0",
        description=(
            "EconML CausalForestDML for conditional event sensitivities; "
            "Stage 4B placeholder reuses the event-study sensitivity until "
            "real-data CATE is wired in Stage 4C/9."
        ),
        references=[
            "Chernozhukov et al. (2018) Double/Debiased ML",
            "Wager & Athey (2018) Estimation and Inference of Heterogeneous Treatment Effects",
        ],
    )

    def __init__(
        self,
        *,
        event_window: tuple[int, int] = (-1, 1),
        lookback_years: int = 5,
        forward_window_days: int = 10,
        time_decay: str = "linear",
        min_events_for_estimate: int = 5,
        kinds: tuple[str, ...] = DEFAULT_EVENT_KINDS,
        importance: tuple[str, ...] = DEFAULT_IMPORTANCE,
    ) -> None:
        if not _econml_available():
            raise RuntimeError(
                "CausalCatalyst requires the optional `[ml]` extra; install with "
                "`uv sync --extra ml` or omit this method from the registry."
            )
        # Reuse the baseline's plumbing; signal differs only when
        # CATE estimation lands (Stage 4C/9).
        self._inner = EventStudyCatalyst(
            event_window=event_window,
            lookback_years=lookback_years,
            forward_window_days=forward_window_days,
            time_decay=time_decay,
            min_events_for_estimate=min_events_for_estimate,
            kinds=kinds,
            importance=importance,
        )

    @property
    def _state(self) -> dict[str, Any] | None:
        return self._inner._state

    def fit_on_session(
        self, session: Session, as_of: object, instrument_ids: list[str]
    ) -> None:
        self._inner.fit_on_session(session, as_of, instrument_ids)

    def serialize(self) -> bytes:
        return self._inner.serialize()

    @classmethod
    def deserialize(cls, blob: bytes) -> CausalCatalyst:
        m = cls()
        if blob:
            m._inner._state = pickle.loads(blob)
        return m

    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        outputs = self._inner.compute(data, session)
        # Re-tag the method_id so consumers see catalyst.causal.v1 not
        # catalyst.event_study.v1 in the metadata blob.
        for o in outputs:
            if isinstance(o.metadata, dict):
                o.metadata["method_id"] = self.metadata.method_id
                o.metadata["placeholder_for_cate"] = True
        return outputs


__all__ = ["CausalCatalyst", "EventStudyCatalyst", "_econml_available"]
