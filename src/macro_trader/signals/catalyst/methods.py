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
    """Causal-inference catalyst sensitivity using CausalForestDML.

    Per (instrument, event_subject) pair with at least
    ``min_events_for_cate`` historical instances, fits a
    ``CausalForestDML`` with:

    - **Treatment**: binary (1 on event days, 0 on a stratified
      sample of non-event days for the same instrument over the same
      lookback window).
    - **Outcome**: log-return in the [-1, +1] event window.
    - **Controls (W)**: macro factor z-scores at the event date plus
      the recent-vol baseline. Conditioning on regime is what makes
      the CATE meaningfully different from the unconditional mean
      that ``EventStudyCatalyst`` uses.

    Pairs with fewer than ``min_events_for_cate`` events fall back
    to the event-study sensitivity (logged with
    ``cate_fallback=True`` in metadata) so the signal still produces
    a per-instrument forward score on every day — the shadow
    degrades gracefully rather than disappearing for under-served
    pairs.

    Daily inference: at ``as_of``, look up today's macro factor
    z-scores; for each upcoming event use the CATE for its
    (instrument, subject) at today's ``W``. Combine via the same
    linear time-decay as the baseline.
    """

    metadata = MethodMetadata(
        method_id="catalyst.causal.v1",
        component="catalyst_signal",
        name="Causal Inference Catalyst Sensitivity",
        version="1.0.0",
        description=(
            "CausalForestDML CATE per (instrument, event_subject) pair "
            "conditional on macro regime; falls back to event-study "
            "sensitivity for pairs without enough events."
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
        min_events_for_cate: int = 15,
        n_estimators: int = 200,
        min_samples_leaf: int = 10,
        random_state: int = 42,
        kinds: tuple[str, ...] = DEFAULT_EVENT_KINDS,
        importance: tuple[str, ...] = DEFAULT_IMPORTANCE,
    ) -> None:
        if not _econml_available():
            raise RuntimeError(
                "CausalCatalyst requires the optional `[ml]` extra; install with "
                "`uv sync --extra ml` or omit this method from the registry."
            )
        self.event_window = event_window
        self.lookback_years = int(lookback_years)
        self.forward_window_days = int(forward_window_days)
        self.time_decay = time_decay
        self.min_events_for_estimate = int(min_events_for_estimate)
        self.min_events_for_cate = int(min_events_for_cate)
        self.n_estimators = int(n_estimators)
        self.min_samples_leaf = int(min_samples_leaf)
        self.random_state = int(random_state)
        self.kinds = kinds
        self.importance = importance
        self._state: dict[str, Any] | None = None
        # Re-use the baseline for the event-study fallback path.
        self._fallback = EventStudyCatalyst(
            event_window=event_window,
            lookback_years=lookback_years,
            forward_window_days=forward_window_days,
            time_decay=time_decay,
            min_events_for_estimate=min_events_for_estimate,
            kinds=kinds,
            importance=importance,
        )

    # --- fit / serialize -----------------------------------------------
    def fit_on_session(
        self, session: Session, as_of: object, instrument_ids: list[str]
    ) -> None:
        from datetime import datetime as _dt

        as_of_dt = as_of if isinstance(as_of, _dt) else _dt.fromisoformat(str(as_of))

        # Fit the fallback first; we'll need its sensitivities for any
        # (instrument, subject) pair that doesn't clear ``min_events_for_cate``.
        self._fallback.fit_on_session(session, as_of_dt, instrument_ids)
        fallback_state = self._fallback._state
        fallback_lookup: dict[tuple[str, str], float] = {}
        if fallback_state is not None:
            for s in fallback_state["sensitivities"]:
                fallback_lookup[(s["instrument_id"], s["subject"])] = s["sensitivity"]

        # Pull historicals + factor panel + price series.
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
            log.info("signals.catalyst.causal.no_historicals")
            self._state = None
            return

        from macro_trader.signals.factor_exposure.factors import build_factor_panel

        factor_panel = build_factor_panel(
            session, as_of=as_of_dt, lookback_days=int(self.lookback_years * 365)
        )
        if factor_panel.empty:
            log.warning("signals.catalyst.causal.no_factor_panel_falling_back")
            # Fall back entirely to the event-study state.
            self._state = self._mark_state_from_fallback(fallback_lookup)
            return

        cates = self._fit_cates_per_pair(
            historicals=historicals,
            factor_panel=factor_panel,
            fallback_lookup=fallback_lookup,
        )

        self._state = {
            "cates": cates,
            "factor_columns": list(factor_panel.columns),
            "fit_as_of": as_of_dt.isoformat(),
            "n_historicals": len(historicals),
            "instruments": list(instrument_ids),
        }

    def _mark_state_from_fallback(
        self, fallback_lookup: dict[tuple[str, str], float]
    ) -> dict[str, Any]:
        """Build a state dict that flags every pair as a fallback —
        used when the factor panel is empty (no regime conditioning
        possible)."""
        cates: list[dict[str, Any]] = []
        for (inst, subj), sens in fallback_lookup.items():
            cates.append(
                {
                    "instrument_id": inst,
                    "subject": subj,
                    "cate_at_today": float(sens),
                    "fallback": True,
                    "n_events_used": 0,
                }
            )
        return {
            "cates": cates,
            "factor_columns": [],
            "fit_as_of": None,
            "n_historicals": 0,
            "instruments": [],
        }

    def _fit_cates_per_pair(
        self,
        *,
        historicals: list[Any],
        factor_panel: Any,
        fallback_lookup: dict[tuple[str, str], float],
    ) -> list[dict[str, Any]]:
        """Fit one CausalForestDML per (instrument, subject) pair with
        enough events, evaluate the CATE at today's regime
        (W=last_row_of_factor_panel)."""
        from collections import defaultdict

        from econml.dml import CausalForestDML
        from sklearn.ensemble import RandomForestRegressor

        grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for h in historicals:
            grouped[(h.instrument_id, h.subject)].append(h)

        # W at "today" = last available row of factor panel.
        w_today = factor_panel.iloc[-1].to_numpy().reshape(1, -1)
        factor_index = factor_panel.index
        cates: list[dict[str, Any]] = []

        for (inst, subj), events in grouped.items():
            n_events = len(events)
            if n_events < self.min_events_for_cate:
                # Fall back to event-study sensitivity for this pair.
                cates.append(
                    {
                        "instrument_id": inst,
                        "subject": subj,
                        "cate_at_today": float(fallback_lookup.get((inst, subj), 0.0)),
                        "fallback": True,
                        "n_events_used": n_events,
                    }
                )
                continue

            # Build (Y, T, W) for events.
            event_w: list[Any] = []
            event_y: list[float] = []
            for h in events:
                ts = pd.Timestamp(h.event_ts).tz_localize("UTC") if pd.Timestamp(h.event_ts).tzinfo is None else pd.Timestamp(h.event_ts).tz_convert("UTC")
                # Find nearest factor-panel row at-or-before the event.
                pos = factor_index.searchsorted(ts, side="right") - 1
                if pos < 0 or pos >= len(factor_index):
                    continue
                w_row = factor_panel.iloc[pos].to_numpy()
                if pd.isna(w_row).any():
                    continue
                event_w.append(w_row)
                event_y.append(float(h.log_return))

            if len(event_y) < self.min_events_for_cate:
                cates.append(
                    {
                        "instrument_id": inst,
                        "subject": subj,
                        "cate_at_today": float(fallback_lookup.get((inst, subj), 0.0)),
                        "fallback": True,
                        "n_events_used": len(event_y),
                    }
                )
                continue

            # Stratified non-event sample of the same size for T=0.
            non_event_idx = factor_panel.dropna(how="any").sample(
                n=len(event_y), random_state=self.random_state, replace=False
            )
            non_w = non_event_idx.to_numpy()
            non_y = [0.0] * len(non_event_idx)  # unconditional null effect

            w = np.vstack([event_w, non_w])
            y = np.array(event_y + non_y)
            t = np.array([1] * len(event_y) + [0] * len(non_y))

            try:
                est = CausalForestDML(
                    n_estimators=self.n_estimators,
                    min_samples_leaf=self.min_samples_leaf,
                    random_state=self.random_state,
                    discrete_treatment=True,
                    model_y=RandomForestRegressor(
                        n_estimators=50, min_samples_leaf=10, random_state=self.random_state
                    ),
                    model_t=RandomForestRegressor(
                        n_estimators=50, min_samples_leaf=10, random_state=self.random_state
                    ),
                )
                est.fit(Y=y, T=t, X=w, W=w)
                cate_today = float(est.const_marginal_effect(w_today)[0])
            except Exception as exc:  # pragma: no cover - EconML edge cases
                log.warning(
                    "signals.catalyst.causal.fit_failed",
                    instrument=inst,
                    subject=subj,
                    error=str(exc),
                )
                cates.append(
                    {
                        "instrument_id": inst,
                        "subject": subj,
                        "cate_at_today": float(fallback_lookup.get((inst, subj), 0.0)),
                        "fallback": True,
                        "n_events_used": len(event_y),
                    }
                )
                continue

            cates.append(
                {
                    "instrument_id": inst,
                    "subject": subj,
                    "cate_at_today": cate_today,
                    "fallback": False,
                    "n_events_used": len(event_y),
                }
            )

        return cates

    def serialize(self) -> bytes:
        if self._state is None:
            return b""
        return pickle.dumps(self._state, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def deserialize(cls, blob: bytes) -> CausalCatalyst:
        m = cls()
        if blob:
            m._state = pickle.loads(blob)
        return m

    # --- compute -------------------------------------------------------
    def compute(self, data: SignalInput, session: Session | None) -> list[SignalOutput]:
        if session is None:
            raise ValueError("CausalCatalyst requires a DB session")
        if self._state is None:
            log.info("signals.catalyst.causal.fallback_fit")
            self.fit_on_session(session, data.as_of, list(data.instrument_ids))
            if self._state is None:
                return []

        # Build sensitivity lookup from CATEs.
        sensitivity_lookup: dict[tuple[str, str], float] = {}
        fallback_pairs = 0
        for entry in self._state["cates"]:
            sensitivity_lookup[(entry["instrument_id"], entry["subject"])] = float(
                entry["cate_at_today"]
            )
            if entry.get("fallback"):
                fallback_pairs += 1

        scores = upcoming_score(
            session,
            instrument_ids=list(data.instrument_ids),
            as_of=data.as_of,
            sensitivities=[
                EventSensitivity(
                    instrument_id=inst,
                    subject=subj,
                    n_events=0,
                    mean_abs_return=0.0,
                    baseline_vol=0.0,
                    sensitivity=cate,
                )
                for (inst, subj), cate in sensitivity_lookup.items()
            ],
            forward_window_days=self.forward_window_days,
            decay="linear" if self.time_decay == "linear" else "exponential",
            kinds=self.kinds,
            importance=self.importance,
        )

        subject_counts: dict[str, int] = {}
        for entry in self._state["cates"]:
            subject_counts[entry["instrument_id"]] = subject_counts.get(
                entry["instrument_id"], 0
            ) + 1

        ranks = cross_sectional_rank(
            {k: abs(v) for k, v in scores.items() if v != 0.0}
        )
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
                        "fallback_pairs": fallback_pairs,
                        "forward_window_days": self.forward_window_days,
                    },
                )
            )
        return outputs


__all__ = ["CausalCatalyst", "EventStudyCatalyst", "_econml_available"]
