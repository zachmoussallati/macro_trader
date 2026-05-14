"""Ensemble helpers used by trend (and later, every family that combines methods).

The current Stage 3 combine is a fixed equal-weighted average; the
``regime_state`` arg is wired through for Stage 6 but ignored here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from macro_trader.signals.base import SignalOutput


def equal_weighted(
    component_outputs: Mapping[str, list[SignalOutput]],
    *,
    weights: Mapping[str, float] | None = None,
    regime_state: str | None = None,
) -> list[SignalOutput]:
    """Combine multiple methods' outputs into an ensemble.

    Inputs are grouped by ``(instrument_id, value_ts)``; each cohort is
    averaged with the supplied weights (default equal). Confidence is the
    weighted mean of component confidences; rolling Sharpe is averaged the
    same way. Metadata records which components contributed.
    """
    if not component_outputs:
        return []

    method_ids = list(component_outputs.keys())
    if weights is None:
        w = {mid: 1.0 / len(method_ids) for mid in method_ids}
    else:
        # Filter to present components and renormalise so missing pieces
        # don't silently down-weight the ensemble.
        filtered = {mid: weights[mid] for mid in method_ids if mid in weights}
        total = sum(filtered.values()) or 1.0
        w = {mid: filtered[mid] / total for mid in filtered}

    # Group by (instrument_id, value_ts).
    cohort: dict[tuple[str, Any], dict[str, SignalOutput]] = {}
    for method_id, outputs in component_outputs.items():
        if method_id not in w:
            continue
        for out in outputs:
            key = (out.instrument_id, out.value_ts)
            cohort.setdefault(key, {})[method_id] = out

    combined: list[SignalOutput] = []
    for (instrument_id, value_ts), parts in cohort.items():
        total_weight = sum(w[mid] for mid in parts)
        if total_weight <= 0:
            continue
        raw = sum(parts[mid].raw_value * w[mid] for mid in parts) / total_weight
        confidence = sum(parts[mid].confidence * w[mid] for mid in parts) / total_weight
        zscore = sum(parts[mid].zscore * w[mid] for mid in parts) / total_weight
        rank = sum(parts[mid].rank * w[mid] for mid in parts) / total_weight
        rolling = _weighted_mean_optional(
            [(parts[mid].rolling_sharpe_252, w[mid]) for mid in parts]
        )
        observation_ts = max(parts[mid].observation_ts for mid in parts)
        combined.append(
            SignalOutput(
                instrument_id=instrument_id,
                value_ts=value_ts,
                observation_ts=observation_ts,
                raw_value=raw,
                zscore=zscore,
                rank=rank,
                confidence=confidence,
                rolling_sharpe_252=rolling,
                metadata={
                    "components": {mid: w[mid] for mid in parts},
                    "weighting_scheme": "equal_weighted",
                },
            )
        )
    return combined


def _weighted_mean_optional(
    values: list[tuple[float | None, float]],
) -> float | None:
    pairs = [(v, w) for v, w in values if v is not None and w > 0]
    if not pairs:
        return None
    total_weight = sum(w for _, w in pairs)
    if total_weight <= 0:
        return None
    return sum(v * w for v, w in pairs) / total_weight
