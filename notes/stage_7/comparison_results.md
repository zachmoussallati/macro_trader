# Stage 7 — Comparison Results

The composite comparator is the most consequential comparator in
the system. Composite scores drive every downstream trading
decision; two composite methods that disagree on the top-N long
ideas are producing materially different trade lists.

## What the comparator measures

`CompositeComparator._compute_metrics()` returns:

| metric                          | how to read                                                                 |
| ------------------------------- | --------------------------------------------------------------------------- |
| `n_observations`                | count of (instrument, value_ts) rows in the intersection                    |
| `direction_agreement`           | fraction of rows where `sign(score_a) == sign(score_b)`                     |
| `rank_correlation`              | mean cross-sectional Spearman correlation per value_ts                      |
| `value_correlation`             | Pearson of raw scores across the intersection                               |
| **`top_5_overlap`**             | mean fraction of method A's top-5 longs that appear in method B's top-5     |
| **`bottom_5_overlap`**          | same for shorts                                                             |
| `confidence_a` / `_b`           | mean confidence reported by each method                                     |
| `n_signals_used_a/b_median`     | median count of contributing signals per row                                |

`top_5_overlap` and `bottom_5_overlap` are load-bearing: they
quantify whether two methods agree on tradeable opportunities,
not just on directional bias.

## Expected baselines (no live data yet)

With the same underlying signal_values and the same regime
classifier output, all 3 composite methods should produce highly-
correlated scores. Pre-deployment expectations:

| pair                                         | direction_agreement | value_correlation | top_5_overlap |
| -------------------------------------------- | ------------------- | ----------------- | ------------- |
| `linear.v1`     vs `bayesian_hier.v1`        | 0.92 – 0.98         | 0.85 – 0.95       | 0.70 – 0.85   |
| `linear.v1`     vs `gbm.v1`                  | 0.85 – 0.95         | 0.65 – 0.85       | 0.55 – 0.75   |
| `bayesian_hier` vs `gbm.v1`                  | 0.85 – 0.95         | 0.70 – 0.85       | 0.60 – 0.75   |

Rationale:

- **direction_agreement** is high because all three consume the
  same signals; the *sign* of the composite is dominated by the
  signs of the underlying z-scores.
- **value_correlation** between linear and Bayesian is high
  because both apply the same probability-weighted blending; the
  difference is per-(regime, instrument) shrinkage vs flat
  per-regime weighting. GBM correlates less because it captures
  non-linear interactions the others can't.
- **top_5_overlap** is the meaningful divergence metric. Even
  with high value_correlation, the ranking of mid-strength scores
  can shuffle. 70% overlap (3.5 / 5) means consensus on the top
  ideas; 50% (2.5 / 5) suggests genuinely different views worth
  investigating.

## Initial production seeding

When the system goes live:

1. Linear baseline runs daily from the start.
2. Bayesian + GBM run as shadows for 90 days collecting
   `CompositeComparator` rows.
3. After 90 days, query
   `/signals/comparisons?component=composite_score` and inspect
   `top_5_overlap` rolling 30-day mean per pair.

Promotion thresholds (per `methods.default_promotion` policy):

| metric             | threshold for shadow promotion                       |
| ------------------ | ---------------------------------------------------- |
| top_5_overlap      | must be `>= 0.6` (i.e. shadow agrees with baseline 3/5 on top picks) |
| direction_agreement | `>= 0.85`                                            |
| value_correlation  | `>= 0.7`                                             |
| n_observations     | `>= 90` days × 13 instruments = `>= 1170` rows       |
| improvement margin | shadow's `confidence` median must exceed baseline by `>= 5%` OR shadow's prediction error vs realised return must be `<= 95%` of baseline's |

If the shadow can't clear `top_5_overlap >= 0.6` it should
*never* be promoted — it would generate qualitatively different
trade lists every day, which is bad UX for operators and bad
behaviour for a strategy that's supposed to be incrementally
improving.

## Per-pair degradation alerts

Stage 13 (Monitoring) will turn these into Dagster sensors. Until
then, manually:

```bash
curl 'http://localhost:8000/api/v1/signals/comparisons?component=composite_score'
```

Watch for:

- `top_5_overlap` dropping below 0.5 for >7 days running. The
  shadow has drifted away from the baseline; investigate whether
  the shadow's most-recent refit picked up new structure or
  whether the baseline is missing a regime shift.
- `value_correlation` dropping below 0.5. The two methods are now
  using *materially different signal weights*; their underlying
  agreement isn't there. Most likely culprit: a weight snapshot
  refresh that put very different per-regime weights on a
  recently-added signal family.
- `confidence_a` or `confidence_b` dropping below 0.3. Most
  per-row signals are speaking with low conviction. Either the
  signal layer has degraded (check upstream `data_quality_job`)
  or the regime classifier is sitting on an uncertain probability
  vector for a long stretch.

## The Bayesian credible interval (deferred)

When `composite.bayesian_hier.v1` is upgraded from MAP weights to
posterior averaging, the comparator gains:

- `posterior_std_a/b_median`: typical credible-interval width per
  row. A shadow with consistently wider intervals than the
  baseline is honestly admitting uncertainty; a shadow with
  consistently narrower intervals than the baseline is either
  better-calibrated or over-confident.

Tracked in `tradeoffs.md` §2.

## What we'll learn from the first 90 days

Open questions the live data will answer:

1. Does the GBM composite find non-linearities the linear method
   misses, or does it overfit to in-sample noise? `top_5_overlap`
   with linear is the cheap test; walk-forward Sharpe (Stage 9)
   is the rigorous one.
2. Does probability-weighted blending produce smoother weight
   transitions than hard-switching would have? The
   `composite_metadata.regime_probability_vector` column over time
   tells us how often the system is in a mixed-regime state.
3. Does the BOCPD transition dampener fire usefully, or does it
   mostly stay at multiplier=1.0? The `/composite/transition_multiplier`
   history shows this directly.
