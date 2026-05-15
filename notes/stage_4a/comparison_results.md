# Stage 4A — comparison results

What the two new comparators measure, what they record into
`system.method_comparisons`, and how to read the result.

## What `PositioningSignalComparator` measures

Inherits the base `SignalFamilyComparator` metrics
(`n_observations`, `direction_agreement`, `rank_correlation_a_b`,
`value_correlation_a_b`, `rolling_sharpe_a/b`, `stability_a/b`,
`turnover_a/b`, `confidence_a/b`) and adds:

| metric | what it is | how to read |
| --- | --- | --- |
| `extreme_overlap` | When the baseline (managed-money z-score) flags `\|raw_value\| >= tanh(2)`, what fraction of those rows does the shadow (commercial extremes) flag too? | High overlap = the two methodologies agree on the most actionable cases. Low overlap = methodology disagreement worth a manual read. |
| `avg_history_weeks_a` / `_b` | Placeholder — currently `None` (NaN sanitised to JSON `null`). The base-class flattening drops per-row metadata before the family hook sees it; flowing it through is tracked in `tradeoffs.md`. | Once enabled, low values flag periods when neither method has enough COT history to be confident. |

The `direction_agreement` is the workhorse here: positioning signals
are not subtle, so even a noisy difference between methods that
agree on long-bias half the time is informative.

## What `DislocationSignalComparator` measures

Same base-class metrics plus:

| metric | what it is | how to read |
| --- | --- | --- |
| `residual_correlation` | Pearson correlation of the two methods' `raw_value` series across the comparison window. | High correlation (>0.7) = PCA and DFM agree on the cross-section's residual structure. Low correlation = DFM is picking up something PCA misses (time-varying loadings) — exactly the enhancement we want to evaluate. |

Loading-stability — how much do factor loadings change
week-over-week, intended as a promotion signal — is not yet computed
because:

1. The methods don't currently expose fitted loadings (re-fit on each
   compute, no caching).
2. Comparing PCA's discrete-refit loading flips to DFM's smooth Kalman
   updates requires both methods to emit a `loadings_t` series, which
   adds storage / API surface beyond this stage's scope.

Both deferrals are documented in `tradeoffs.md`.

## How to query

```bash
# All comparisons for the positioning family
curl 'http://localhost:8000/api/v1/methods/comparisons?component=positioning_signal' | jq

# Single comparison detail
curl 'http://localhost:8000/api/v1/methods/comparisons/<comparison_id>' | jq '.metrics'

# All comparisons for the dislocation family
curl 'http://localhost:8000/api/v1/methods/comparisons?component=dislocation_signal' | jq
```

The `metrics` / `agreement` / `stability` blobs are JSONB; any NaN
that arises during computation is sanitised to `null` at persistence
time (see `ComparisonResult.to_db_row`).

## Early numbers (placeholder)

The Stage 4A integration test
(`tests/integration/signals/test_positioning_pipeline.py`)
exercises the positioning comparator against seeded ramping COT
data. The resulting comparison row has:

- `direction_agreement` near 1.0 (both methods see the same
  long/short bias because the seed data feeds the same numeric
  series to both report types).
- `extreme_overlap` = 1.0 when both methods reach extreme; 0.0
  early in the seeded history before the z-score window fills.

Once Stage 9's backfill populates a year of real CFTC + price data,
this file should be updated with rolling 30-day averages of:

- `positioning_signal.extreme_overlap` — expected 0.4-0.7 in
  practice (managed-money and commercial extremes only align at
  major reversals).
- `dislocation_signal.residual_correlation` — expected 0.6-0.8
  (PCA and DFM mostly agree; the interesting cases are when they
  don't).
