# Stage 4B — comparison results

What the two new comparators measure and how to read the output.

## What `FactorExposureComparator` measures

Inherits the base `SignalFamilyComparator` metrics plus:

| metric | what it is | how to read |
| --- | --- | --- |
| `value_correlation` | Pearson correlation of the two methods' raw_value series across the comparison window. | High = methods produce similar return-direction signals. Low = a real methodology disagreement worth investigating before promotion. |
| `rank_correlation` | Spearman correlation of the cross-sectional ranks. | More robust than value correlation — tells you whether the two methods agree on *which instruments* are most attractive even if scaling differs. |
| `loading_correlation` | (Pending — see notes/stage_4b/tradeoffs.md §6.) Correlation of factor loadings between methods. | Will let promotion criteria distinguish "same signals via same exposures" (likely a good promotion case) from "same signals via different exposures" (suspicious — the overlap may be coincidental). |

The three-way comparison (OLS + RF + CF) produces two
`ComparisonResult` rows per run:

- `(factor_exposure.ols.v1, factor_exposure.rf.v1)`
- `(factor_exposure.ols.v1, factor_exposure.causal_forest.v1)` — only
  when EconML is installed.

Stage 4A's `run_comparisons_for_component` runner is the first time
we exercise N>1 shadows per component. The runner skips if
`reference_for(component)` returns `None` or if there are no shadows
registered.

## What `CatalystSignalComparator` measures

Inherits base `SignalFamilyComparator` metrics plus:

| metric | what it is | how to read |
| --- | --- | --- |
| `value_correlation` | Pearson correlation of forward-score raw values. | Until Stage 4C, the placeholder causal method delegates to the event-study, so this will be ~1.0 — *expected behaviour for Stage 4B*, not signal agreement. |

## How to query

```bash
# All factor exposure comparisons
curl 'http://localhost:8000/api/v1/methods/comparisons?component=factor_exposure_signal' | jq

# All catalyst comparisons
curl 'http://localhost:8000/api/v1/methods/comparisons?component=catalyst_signal' | jq

# Single comparison detail
curl 'http://localhost:8000/api/v1/methods/comparisons/<comparison_id>' | jq '.metrics'
```

Sanitisation: `metrics`, `agreement`, `stability` JSONB blobs are
NaN-sanitised at persist time (Stage 4A added `_sanitize_nans` in
`ComparisonResult.to_db_row`).

## Early numbers (placeholder)

Stage 4B does not include real-data integration tests for either
new comparator (factor exposure refit needs ~252 days of synthetic
+ FRED panel; catalyst refit needs seeded calendar events plus
matching price panels). Once Stage 5 / Stage 9 land:

- **Factor exposure**: expect `value_correlation` between OLS and
  RF to be 0.4-0.7 in practice (RF picks up non-linear effects OLS
  misses, but most of the directional signal is shared linear
  exposure).
- **Catalyst**: until Stage 4C wires real CATE into
  `CausalCatalyst`, `value_correlation` will be 1.0. Stage 4C
  baseline expectation: ~0.7-0.9 (conditional sensitivities are
  related to but distinct from unconditional means).
