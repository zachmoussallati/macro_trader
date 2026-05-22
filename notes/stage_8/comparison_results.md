# Stage 8 — Comparison Results

Two comparators run daily:

- `CompositeComparator` (Stage 7) — composite scores agreement
  (`top_5_overlap` is load-bearing).
- `PortfolioConstructionComparator` (Stage 8) — sized positions
  agreement (`position_sign_agreement` is load-bearing).

This note covers the Stage 8 portfolio-construction comparator.
The covariance methods (LW vs DCC-GARCH) get compared via
`MethodComparator`'s base implementation; no specialised comparator
is needed because the outputs are matrices, and matrix-level
metrics (Frobenius norm, max abs difference) are captured by the
existing framework.

## What the portfolio comparator measures

`PortfolioConstructionComparator._compute_metrics()` returns:

| metric                      | how to read                                                                |
| --------------------------- | -------------------------------------------------------------------------- |
| `n_observations`            | count of (instrument, value_ts) rows in the intersection                   |
| **`position_sign_agreement`** | fraction of rows where `sign(weight_a) == sign(weight_b)`                |
| `weight_correlation`        | Pearson on signed weights                                                  |
| `rank_correlation`          | Spearman on absolute weights                                               |
| `weight_l1_distance`        | sum of `|weight_a - weight_b|` across instruments                          |
| **`top_3_long_overlap`**    | of method A's top-3 longs, how many appear in method B's top-3 longs       |
| **`top_3_short_overlap`**   | same for shorts                                                            |
| `expected_vol_a/b`          | each method's reported portfolio vol                                       |

`position_sign_agreement`, `top_3_long_overlap`, and
`top_3_short_overlap` are the load-bearing metrics.

## Expected baselines (no live data yet)

Since all 4 methods consume the same composite scores + same
covariance, and all enforce the same composite-sign overlay, the
sign agreement should be near-perfect. Pre-deployment
expectations:

| pair                       | sign_agreement | top_3_long_overlap | weight_correlation |
| -------------------------- | -------------- | ------------------ | ------------------ |
| ERC vs HRP                 | 0.98 – 1.00    | 0.65 – 0.85        | 0.80 – 0.95        |
| ERC vs BL                  | 0.95 – 1.00    | 0.55 – 0.75        | 0.65 – 0.85        |
| ERC vs CVaR                | 0.95 – 1.00    | 0.50 – 0.70        | 0.55 – 0.75        |
| HRP vs BL                  | 0.95 – 1.00    | 0.50 – 0.70        | 0.60 – 0.80        |
| HRP vs CVaR                | 0.95 – 1.00    | 0.50 – 0.70        | 0.55 – 0.75        |
| BL vs CVaR                 | 0.95 – 1.00    | 0.55 – 0.75        | 0.60 – 0.80        |

Rationale:

- **Sign agreement near-perfect**: all 4 methods enforce
  `sign(weight) == sign(composite_score)` post-optimisation. The
  rare disagreement is when a method's eligibility filter drops
  a position the other keeps (e.g. min_positions threshold).
- **Top-3 long/short overlap moderate**: methods diverge most on
  *which* names get the biggest positions. ERC concentrates on
  low-vol instruments; HRP concentrates on cluster-medium-risk;
  BL concentrates on high-confidence views; CVaR concentrates on
  positions that hedge the tail.
- **Weight correlation moderate**: even when methods agree on
  sign, magnitudes can differ substantially. Pearson is sensitive
  to the size of those differences.

## Initial production seeding

When the system goes live:

1. ERC baseline runs daily from the start.
2. HRP / BL / CVaR run as shadows for 60 days collecting
   `PortfolioConstructionComparator` rows.
3. After 60 days, query
   `/signals/comparisons?component=portfolio_construction` and
   inspect rolling 30-day means per pair.

Promotion thresholds (per `methods.default_promotion` policy +
Stage 8 conventions):

| metric                      | threshold for shadow promotion to PRODUCTION |
| --------------------------- | --------------------------------------------- |
| position_sign_agreement     | `>= 0.95` (any candidate below this is making different directional calls) |
| top_3_long_overlap          | `>= 0.55`                                     |
| top_3_short_overlap         | `>= 0.55`                                     |
| weight_correlation          | `>= 0.65`                                     |
| expected_vol_b              | within 10% of expected_vol_a                  |
| n_observations              | `>= 60 days × 13 instruments = >= 780 rows`   |
| backtester improvement      | Stage 9 walk-forward Sharpe `>= ERC + 0.10`   |

The backtester improvement is the *new* promotion criterion for
Stage 8+. Composite shadow promotions (Stage 7) had no realised-
return validation because the composite is one step removed from
trade decisions. Portfolio shadows go straight to position sizes,
so OOS Sharpe is the only honest test.

## Per-pair degradation alerts

Stage 13 (Monitoring) will turn these into Dagster sensors. Until
then, manually:

```bash
curl 'http://localhost:8000/api/v1/signals/comparisons?component=portfolio_construction'
```

Watch for:

- `position_sign_agreement` dropping below 0.90 for >5 days
  running. A persistent sign disagreement suggests one method's
  eligibility filter is dropping different names than another's
  — probably a constraint mismatch (different `min_positions`
  thresholds in custom configs).
- `top_3_long_overlap` or `top_3_short_overlap` consistently
  below 0.40. Methods diverging materially on biggest positions
  means at least one of them is overfitting to a feature the
  others don't see — investigate.
- `weight_correlation` flipping sign (positive → negative across
  weeks). Indicates a method is now sizing *against* the
  baseline; serious issue.

## Covariance comparison metrics

The Ledoit-Wolf vs DCC-GARCH comparison runs through the base
`MethodComparator` framework. Key metrics emitted to
`system.method_comparisons`:

- Output correlation: Frobenius-norm distance between the two
  daily covariance matrices.
- Output agreement: rank correlation of the off-diagonal entries
  (do both methods rank cross-instrument correlations the same
  way?).

Expected:

| pair                                         | correlation | rank_agreement |
| -------------------------------------------- | ----------- | -------------- |
| LW vs DCC-GARCH (steady regime)              | 0.85 – 0.95 | 0.80 – 0.92    |
| LW vs DCC-GARCH (regime transition)          | 0.50 – 0.75 | 0.60 – 0.80    |

The interesting case is the second row: that's where DCC-GARCH's
regime-conditional behaviour earns its shadow promotion. If
Stage 9 backtester shows DCC-GARCH-driven portfolios survive
2008-style correlation breakdowns materially better than LW-driven
ones, it earns promotion.

## What we'll learn from the first 60 days

Open questions the live data will answer:

1. Does HRP's no-matrix-inversion property actually produce
   stable weights in stressed periods? `weight_l1_distance(ERC, HRP)`
   should *decrease* during high-VIX windows if HRP is more
   robust.
2. Does Black-Litterman over-weight high-confidence-but-wrong
   composite signals? The `tau / view_confidence_floor` knobs
   would need re-tuning if so.
3. Does CVaR's Monte Carlo (Gaussian) loss generation
   under-estimate tail risk vs realised? Compare CVaR's claimed
   `expected_vol` against realised vol weekly; a persistent
   over-claim suggests bootstrap-based scenarios are needed.
4. Drawdown gate fire rate: how often does L1 trigger in a
   typical year? L2 / L3? The thresholds were calibrated for 12%
   vol; if realised vol diverges, the gates may need
   re-calibration.
