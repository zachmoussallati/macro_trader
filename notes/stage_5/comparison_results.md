# Stage 5 — comparison results

## New comparators

| Comparator | Component | Pair compared |
| --- | --- | --- |
| `NowcastingSignalComparator` | nowcasting_signal | `(ols_ar, bvar)` |
| `VolSurfaceSignalComparator` | vol_surface_signal | `(raw, svi)` |

Both follow the Stage 4A pattern (subclass of
`SignalFamilyComparator`, override `_extra_metrics`) and produce
one `ComparisonResult` per daily run via the standard
`run_comparisons_for_component` runner.

Alt-data has **no comparator** (Stage 5 prompt + decisions.md §7).

## What `NowcastingSignalComparator` measures

Inherits the base metrics (direction_agreement, rank_correlation,
n_observations) plus:

| metric | what it is |
| --- | --- |
| `value_correlation` | Pearson correlation of OLS and BVAR `raw_value` (post-tanh surprises). |

Expected range when both methods are fitted to real data:

- High correlation (>0.85): the BVAR's prior shrinkage barely
  changes the OLS estimate — usually means the OLS regression has
  enough effective sample size that the prior contributes little.
- Mid range (0.5-0.85): BVAR is shrinking small-sample noise; the
  shadow is genuinely differentiated. Promotion candidate.
- Low (<0.5): BVAR's prior is dominating the data — investigate
  `lambda_minnesota` tuning before promoting.

## What `VolSurfaceSignalComparator` measures

Inherits the base metrics plus:

| metric | what it is |
| --- | --- |
| `value_correlation` | Pearson correlation of raw-chain and spline-fitted composite scores. |

For vol surface, expected range:

- High correlation (>0.95): the spline is fitting tightly to the
  raw quotes, so the smoothed metrics differ only slightly. This
  is normal when chains are dense (SPY, GLD); the SVI shadow's
  value is in arbitrage detection rather than score divergence.
- Low correlation: thin chains where the spline extrapolates
  aggressively. UNG / DBA often fall here in practice.

## Stage 5 limitations on real-comparator readings

1. **Vol surface**: until the chains table is populated regularly
   (Stage 5 ships the ingester but not a Dagster asset to run it
   daily — see usage.md), the comparator has no data to compare
   on. Stage 6 should wire `ingest_options_chains` as a daily
   asset upstream of `signal_vol_surface`.
2. **Nowcasting**: requires accumulated FRED data and at least a
   few release cycles before the OLS-AR / BVAR pair produces
   meaningfully different rows. Until that lands the comparator's
   `value_correlation` will read either undefined (no overlapping
   rows) or near 1.0 (both methods saw too little data and fell
   back to the same baseline).

## Stage 4B + 4C carryover still applies

The Methods page differentiation badge from Stage 4C reads each
component's most-recent comparator row. After Stage 5:

- `nowcasting_signal`: badge will read "no data" until the first
  weekly refit + daily comparator run lands.
- `vol_surface_signal`: badge will read "no data" until the chain
  ingester is wired (per item 1 above).
- `alt_data_signal`: badge reads "no data" permanently — there is
  no comparator for this family by design. Stage 6 could either
  hide the badge for the alt_data component or add a
  "no comparator (intentional)" explicit state.

## Promotion gate state (snapshot post-Stage-5)

```
component                  | baseline                       | shadows                              | status
---------------------------|--------------------------------|--------------------------------------|------------------
trend_signal               | trend.ensemble.v1              | trend.hp_filter.v1                   | needs Stage 9 data
carry_signal               | carry.spot_proxy.v1            | (none — placeholder)                 | n/a
value_signal               | value.zscore.v1                | value.cross_sectional.v1             | needs Stage 9 data
positioning_signal         | positioning.cot_zscore.v1      | positioning.cot_commercial.v1        | needs Stage 9 data
dislocation_signal         | dislocation.pca.v1             | dislocation.dfm.v1                   | needs Stage 9 data
factor_exposure_signal     | factor_exposure.ols.v1         | factor_exposure.rf.v1, .causal_forest.v1 | needs Stage 9 data
catalyst_signal            | catalyst.event_study.v1        | catalyst.causal.v1                   | needs Stage 9 data
alt_data_signal            | (3 baselines, intentional)     | none                                 | n/a (composite handles)
nowcasting_signal          | nowcasting.ols_ar.v1           | nowcasting.bvar.v1                   | needs Stage 9 data
vol_surface_signal         | vol_surface.raw.v1             | vol_surface.svi.v1                   | BLOCKED on paid data
```
