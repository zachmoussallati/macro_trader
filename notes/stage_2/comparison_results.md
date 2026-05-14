# Stage 2 — comparison results

The `DataQualityComparator` is the first production comparator. It runs
daily via the `daily_data_quality` Dagster asset and persists one row to
`system.method_comparisons` per active instrument.

## What the comparator measures

Both `data_quality.zscore.v1` (BASELINE) and `data_quality.isoforest.v1`
(SHADOW) consume the same 1-D close-price array for an instrument and
return a `bool` mask. The comparator computes:

| Key | Definition | Typical range |
| --- | --- | --- |
| `flag_rate_a` | fraction of inputs the baseline flagged | 0.000–0.030 |
| `flag_rate_b` | fraction of inputs the shadow flagged | 0.005–0.015 (driven by contamination=0.01) |
| `agreement_iou` | `|A ∩ B| / |A ∪ B|` of the two flag masks | 0.0–1.0 |
| `agreement_correlation` | Pearson on the 0/1 vectors | -1.0–1.0 |
| `n_observations` | input length | 100–365 |

`agreement` and `stability` blocks on the `ComparisonResult` get the
generic helpers from `macro_trader.methods.metrics`:

- `agreement.exact_match_rate` (when both outputs are categorical)
- `agreement.mae`, `agreement.rmse`, `agreement.pearson_correlation`,
  `agreement.spearman_rank_correlation`
- `stability.mean_perturbation_drift_a` and `…_b`

## Expected early signals

After a few weeks of daily runs we expect:

- **`flag_rate_a` ~ 0.005–0.02** on clean ETF series. The z-score
  threshold of 3 with a 60-day window is conservative.
- **`flag_rate_b` ≈ 0.01** by construction (`contamination=0.01`).
- **`agreement_iou` ~ 0.20–0.50** for typical commodities. Both methods
  catch obvious outliers (covid-era prints, geopolitical shocks) but
  disagree on the borderline cases.
- **Stability**: low perturbation drift for both — these are
  deterministic given inputs (z-score) or fixed seed (IsolationForest).

If `agreement_iou` is below 0.10 across all instruments, one of the
methods has a bug or the input data has shifted regimes — first
investigation step.

## Promotion path (proposal)

The default `PromotionCriteria` for `data_quality`:

```python
PromotionCriteria(
    component="data_quality",
    min_shadow_period_days=180,
    min_comparison_runs=120,
    required_improvements=["agreement_iou"],   # placeholder
    improvement_threshold=0.05,
)
```

`agreement_iou` measures agreement between the two methods, not the
shadow's improvement over the baseline. **The comparator as shipped
cannot directly gate promotion** — we need an oracle.

Three options, in order of preference:

1. **Synthetic outlier benchmark.** Pre-Stage 3 work: extend the
   comparator to inject N synthetic outliers per run (known indices),
   then emit `recall_a`, `recall_b`, `precision_a`, `precision_b`. Use
   `recall` as the promotion gate at a matched flag rate.
2. **Heuristic oracle.** Define a stricter rule (e.g. "|z| > 5 over
   the full series") as ground truth. Less rigorous but cheap.
3. **Hand-labelled set.** Curate a few hundred labelled outliers from
   real data; score against the labels. Highest signal, highest cost.

Stage 3 will pick one before the shadow has accumulated enough runs.

## Querying recent comparisons

```bash
curl "http://localhost:8000/api/v1/methods/comparisons?component=data_quality&limit=20" | jq
```

```sql
-- Mean IoU per instrument over the last 30 days
SELECT
  notes,
  COUNT(*) AS runs,
  AVG((metrics->>'agreement_iou')::float) AS mean_iou,
  AVG((metrics->>'flag_rate_a')::float) AS mean_flag_rate_a,
  AVG((metrics->>'flag_rate_b')::float) AS mean_flag_rate_b
FROM system.method_comparisons
WHERE component = 'data_quality'
  AND created_at > now() - INTERVAL '30 days'
GROUP BY notes
ORDER BY mean_iou DESC;
```

(`notes` carries the per-instrument identifier set by `runner.py`.)

## When this file gets concrete data

After the first week of daily runs we'll backfill:

- Table of mean `flag_rate_a`, `flag_rate_b`, `agreement_iou` per
  instrument.
- Top 5 disagreements (the dates where one method flagged and the other
  didn't) — useful for hand-eyeballing whether either method is wrong.
- Stability over time: rolling 30-day IoU per instrument.

These are dashboard tasks in Stage 11; for now the raw rows are in
`system.method_comparisons` and the `/methods/comparisons` API.
