# Stage 3 — comparison results

Each signal family has one comparator that runs daily, persisting to
`system.method_comparisons`. None of them can use forward Sharpe yet
(no backtester); they all measure *agreement* and *stability*.

## What each comparator measures

The shared base (`SignalFamilyComparator`) computes:

| Key | Meaning |
| --- | --- |
| `n_observations` | size of the aligned (inst, value_ts) cohort |
| `direction_agreement` | fraction where `sign(raw_value)` matches |
| `rank_correlation_a_b` | Spearman corr on the cross-sectional rank |
| `value_correlation_a_b` | Pearson corr on raw_value |
| `rolling_sharpe_a` / `_b` | mean in-sample Sharpe per method |
| `stability_a` / `_b` | 1 − fraction of observations where the raw_value changes by > 0.1 between consecutive observations |
| `turnover_a` / `_b` | complement of stability |
| `confidence_a` / `_b` | mean confidence per method |

Plus generic agreement (`pearson_correlation`, `spearman_rank_correlation`,
`mae`, `rmse`) and stability (`mean_perturbation_drift_a`, `_b`) from the
methods-framework base.

## Per-family setup

### Trend
`TrendSignalComparator` runs (baseline = `trend.ensemble.v1`, shadow =
`trend.hp_filter.v1`) daily. The ensemble is the designated production
trend signal; the HP filter is the enhancement candidate.

### Carry
`CarrySignalComparator` exists but is inactive in Stage 3 (only one
method). Will fire when Stage 12 adds the futures-based enhancement.

### Value
`ValueSignalComparator` runs (baseline = `value.zscore.v1`, shadow =
`value.cross_sectional.v1`) daily.

## Expected early signals

After a few daily runs we expect (rough heuristics):

- **direction_agreement** ~0.55–0.75 across families. Identical signals
  would be 1.0; uncorrelated would be 0.5. Below 0.5 indicates the
  shadow is systematically inverse — investigate.
- **rank_correlation_a_b**
  - Trend ensemble vs HP filter: probably 0.3–0.6. They look at different
    aspects of trend (level cross vs cyclical deviation).
  - Value z-score vs cross-sectional: probably 0.5–0.8. The shadow uses
    the same underlying z-score, just ranked differently.
- **turnover** — HP filter typically more stable than SMA crossovers
  (smoothing reduces flip-flops); cross-sectional value should be
  similar to baseline value because the underlying signal is the same.

## Querying recent comparisons

```sql
-- Aggregate metrics over the last 30 days per component:
SELECT
  component,
  count(*) AS runs,
  AVG((metrics->>'direction_agreement')::float) AS dir_agree,
  AVG((metrics->>'rank_correlation_a_b')::float) AS rank_corr,
  AVG((metrics->>'rolling_sharpe_a')::float) AS sharpe_a,
  AVG((metrics->>'rolling_sharpe_b')::float) AS sharpe_b
FROM system.method_comparisons
WHERE component LIKE '%_signal'
  AND created_at > now() - INTERVAL '30 days'
GROUP BY component;
```

## What promotion would require (post-Stage 9)

Once the walk-forward backtester lands, a typical promotion criterion
for an enhancement signal:

```python
PromotionCriteria(
    component="trend_signal",
    min_shadow_period_days=180,
    min_comparison_runs=120,
    required_improvements=["rolling_sharpe_oos"],
    improvement_threshold=0.05,
)
```

- `rolling_sharpe_oos` would be a new metric the Stage 9 comparator
  emits — out-of-sample Sharpe per (instrument, period) under a
  walk-forward scheme.
- 5% relative improvement averaged over 120+ runs is a conservative
  bar.
- Stability and turnover are guard rails, not direct improvement
  targets: a method that flips constantly has zero stability and won't
  be promotable even with a good Sharpe.

## When this file gets concrete numbers

After the first full week of daily runs we'll backfill:

- Trend: mean direction_agreement / rank_correlation per instrument.
- Value: same.
- Carry: still inactive — record this fact and the placeholder
  confidence distribution.

Stage 4's positioning + factor-exposure + dislocation signals will
extend this section once they begin contributing.
