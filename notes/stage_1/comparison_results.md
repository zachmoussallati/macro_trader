# Stage 1 — comparison results

**No comparison runs exist in Stage 1.** No methods have been registered,
so there is nothing to compare. The `system.method_comparisons` table is
empty after `python make.py setup`.

This file is a placeholder; later stages will append a row per comparator
they introduce.

## Where comparison results live

| Layer | Location |
| --- | --- |
| In-process result | `ComparisonResult` dataclass (`src/macro_trader/methods/comparator.py`) |
| Persistent record | `system.method_comparisons` table (one row per `MethodComparator.compare()` call when a session is supplied) |
| API surface | `GET /api/v1/methods/comparisons`, `GET /api/v1/methods/comparisons/{id}` |
| Dashboard | `/methods` page (Stage 11 will add component-grouped comparison cards with deltas + sparklines) |

## Schema of `system.method_comparisons`

| Column | Type | Notes |
| --- | --- | --- |
| `comparison_id` | `VARCHAR(64)` | UUID string, PK |
| `component` | `VARCHAR(64)` | indexed |
| `method_a_id` | `VARCHAR(128)` | typically the baseline / production driver |
| `method_b_id` | `VARCHAR(128)` | typically the shadow / candidate |
| `period_start` | `TIMESTAMPTZ` | the data window the comparison covers |
| `period_end` | `TIMESTAMPTZ` | exclusive |
| `metrics` | `JSONB` | component-specific (see convention below) |
| `agreement` | `JSONB` | generic: `pearson_correlation`, `spearman_rank_correlation`, `exact_match_rate`, `mae`, `rmse` |
| `stability` | `JSONB` | generic: `mean_perturbation_drift_a`, `mean_perturbation_drift_b` |
| `notes` | `TEXT` | free-form |
| `created_at` | `TIMESTAMPTZ` | when the comparator ran |

## `metrics` key convention

Component comparators MUST emit a flat `dict[str, float]`. The convention
that `evaluate_promotion` relies on:

- `<name>_a` — metric value for method A (baseline / production).
- `<name>_b` — metric value for method B (shadow / candidate).
- `<name>` (no suffix) — optional delta or composite (e.g. `<name> = b - a`).

`PromotionCriteria.required_improvements` lists the `<name>` strings;
`evaluate_promotion` looks up `<name>_a` and `<name>_b` and computes
relative improvement.

### Example of well-formed metrics

```jsonc
{
  "sharpe_a": 0.92,
  "sharpe_b": 1.08,
  "sharpe":   0.16,
  "max_dd_a": -0.18,
  "max_dd_b": -0.13,
  "stability_a": 0.94,
  "stability_b": 0.97
}
```

`evaluate_promotion` with `required_improvements=["sharpe","stability"]`
will:

1. Compute `(sharpe_b - sharpe_a) / |sharpe_a|` over every comparison row
   for this `method_b_id` in this `component`. Take the mean.
2. Check it ≥ `improvement_threshold`.
3. Same for `stability`.

## Time / cadence convention

A comparator should be scheduled to run on **non-overlapping** windows
appropriate to the component:

| Component example | Typical cadence | Window size |
| --- | --- | --- |
| Data quality | daily | prior trading day |
| Factor exposure | daily | rolling 60 trading days, walk-forward |
| Regime classifier | weekly | rolling 252 trading days |
| Portfolio construction | weekly | rolling 252 trading days |

Overlapping windows are not forbidden, but each `ComparisonResult` should
represent one decision-relevant slice of time. The Dashboard surfaces
windows individually.

## Reproducibility

- `Method.serialize()` / `deserialize()` round-trips fitted state. When
  promoting, we should also snapshot the fitted blob in
  `system.methods_registry.serialized_blob`.
- Comparisons should be reproducible: same `data` + same fitted blobs ⇒
  same outputs. If your comparison uses random sampling, fix a seed.
- Storage of input data per comparison is NOT done by the framework. The
  Stage 2 data layer will guarantee point-in-time integrity so re-running
  the comparator on a stored period gives the same numbers.

## When this file gets contents

The first row will arrive in Stage 2 (data quality: z-score baseline vs
Isolation Forest enhancement, daily). Stages 3–9 follow with more
comparators.
