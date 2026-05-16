# Stage 4C — comparison results

What changed in the comparator picture once real CATE landed.

## Before / after for the catalyst comparator

| Metric | Stage 4B | Stage 4C |
| --- | --- | --- |
| `value_correlation_a_b` between event_study + causal | 1.0 (placeholder identity) | depends on data; expected 0.4-0.8 once backfill cache lands |
| Catalyst causal `metadata.placeholder_for_cate` | `True` | flag removed |
| Catalyst causal `metadata.fallback_pairs` | n/a | `int` count of pairs that fell back to event-study (typically the majority on small calendars) |

## Before / after for the factor exposure comparator

| Metric | Stage 4B | Stage 4C |
| --- | --- | --- |
| `factor_exposure.causal_forest.v1._state` after refit | `None` (silent fit failure — `est.fit()` missing `X` arg) | populated with per-instrument CATE values |
| `value_correlation_a_b` (OLS vs CF) | n/a (CF never produced rows) | depends on data; expected 0.5-0.9 |

## How to read the dashboard's new differentiation badge

Each component card on `/methods` shows a `ShadowDifferentiationBadge`
driven by `value_correlation_a_b` from the most-recent
`/signals/comparisons?component=...` row:

- **`>= 0.95` (muted "near identical")**: until Stage 4C, every
  catalyst comparison row had this color because `CausalCatalyst`
  was a placeholder. After Stage 4C, this color now means a real
  shadow that didn't differentiate — investigate before promoting.
- **`0.5-0.95` (green "shadow differentiated")**: the desired
  range.
- **`< 0.5` (red "diverged")**: shadow may be too aggressive in
  the other direction — verify the methodology.

## What needs to land for "real" comparison numbers

1. **Backfill cache** (`tests/data/backfill_504d.parquet`) so
   integration tests can exercise the catalyst causal +
   factor_exposure CF refits against ≥504 days of real data.
2. **A few weeks of daily comparison runs** persisting to
   `system.method_comparisons`. The Stage 4B compare_all_signals
   job already does this every day for the existing methods; once
   the EconML-gated methods produce non-empty outputs, the
   comparator runner picks them up automatically (Stage 4A
   `run_comparisons_for_component` handles N>1 shadows per
   component).
3. **At least one real-world catalyst event** since the new CATE
   landed. Until then the catalyst comparator's value_correlation
   reflects the empty-state behaviour (both methods produce 0
   pressure scores when no events are upcoming).

Stage 5 should re-render this file with the actual numbers once
the above three items are in place.

## What the comparators DON'T capture (yet)

Carried over from Stage 4A / 4B tradeoffs:

- `loading_correlation` for factor exposure (planned; needs the
  metadata flow-through fix in `SignalFamilyComparator._compute_metrics`).
- `sensitivity_correlation` for catalyst (same dependency).
- `cate_uncertainty` per pair (EconML's `effect_inference()` API
  produces standard errors; not yet surfaced).
