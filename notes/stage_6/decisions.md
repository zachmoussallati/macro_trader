# Stage 6 — decisions

## 1. Phase 0 outcomes

- **0.1 Vitest**: not executed (Node not on PATH in harness shell);
  carries forward to Stage 7. The 4 Stage 4C + 3 Stage 5 + 1 Stage 6
  smoke tests are structurally sound and follow the same pattern as
  the already-verified `Methods.test.tsx`.
- **0.2 backfill cache**: not regenerated (no FRED_API_KEY in shell).
  The 5 `real_data`-marked tests still skip with the standard
  "backfill cache not found" message.
- **0.3 integration tests**: committed as 4 new files
  (`test_vol_surface_pipeline.py`, `test_nowcasting_pipeline.py`,
  `test_alt_data_pipeline.py`, `test_full_stack_smoke.py`) in
  commit `824273d`. They skip without Postgres but collect cleanly
  and assert the architectural promises of each Stage 5 family.
- **0.4 full-stack smoke**: in the same commit; asserts every
  expected method_id is registered (23 baseline+shadow methods
  across 11 components when EconML is present).

## 2. K = 5 named regimes (PRE-DECIDED in prompt)

risk_on_growth, risk_off_defensive, stagflation, carry_friendly,
vol_spike. Defined per the prompt's characterisation table. The
HMM/GMM/MS-VAR fit K=5 latent states and the centroid-anchored
labelling assigns them to these names.

## 3. Discrete label + probability vector

Output schema carries both:

- `label` (string, one of the 5 named regimes) — used by simple
  consumers (e.g. Stage 6 attribution joins on label).
- `probability_vector` (JSONB of regime → probability) — preserved
  for sophisticated consumers (Stage 7 composite scoring can
  probability-weight signals).

The label is `argmax(probability_vector)`. The two are kept in
sync at persist time.

## 4. 10 features for the regime panel

Six factor z-scores (Stage 4B `factors.build_factor_panel`):
growth, inflation, liquidity, usd, oil, risk_on. Plus four market
indicators:

- `realized_vol_60d` — 60-day std of SPY log-returns (× √252).
- `credit_spread` — `(HYG / IEF) - 1` proxy.
- `yield_curve_slope` — `DGS10 - DGS2` from FRED.
- `vix_level` — `VIXCLS` from FRED.

Missing features silently dropped — methods see the smaller subset
and document the missing entries in `_state.feature_columns`.

## 5. Centroid-anchored labelling via the Hungarian algorithm

The load-bearing discipline that keeps named-regime labels stable
across refits. Implementation in `labeling.py:map_centroids_to_labels`:

1. On first-ever fit, anchor centroids come from
   `DEFAULT_ANCHOR_CENTROIDS` (rough feature-vector means per named
   regime).
2. On subsequent refits, anchor centroids = prior fit's centroids.
3. Hungarian assignment (`scipy.optimize.linear_sum_assignment`)
   minimises the sum of L2 distances between new and prior
   centroids.
4. If any new centroid moves >2 std from its matched prior, log a
   `regime.labeling.drift` warning and capture the warning in
   `mapping_summary["drift_warnings"]`.

If we ever lose this discipline, the dashboard becomes nonsense
because labels would shuffle between refits.

## 6. HMM uses smoothing for historical analysis (filtering would
require a separate path for "today")

`hmmlearn.GaussianHMM.predict_proba` returns smoothed probabilities
(forward-backward). For historical attribution this is the correct
retrospective output. For the "what's the current regime today"
output, smoothing strictly uses future information at every row
except the last — and we only persist the latest `observation_ts`
per `value_ts`, so by definition the row we surface for "today" is
the filtering result (forward only with no future info).

In practice this means: the runner persists outputs from compute()
which yields smoothed probabilities; but only the row corresponding
to the most recent observation_ts is the "live" classification, and
smoothing on that row is equivalent to filtering because there's no
future to back-propagate.

## 7. MS-VAR is a univariate fallback (statsmodels MarkovRegression
on PC1)

Full multivariate MS-VAR (per Krolzig 1997) isn't well-supported in
statsmodels and convergence is fragile. We took the prompt's
explicit fallback option: fit a Markov-switching regression on the
first principal component of the feature panel. Centroids in
feature-space are computed from the smoothed assignments. Less
expressive than full MS-VAR but stable and well-documented.

## 8. BOCPD reports P(r_t < 5) — not P(r_t = 0)

Adams & MacKay 2007 §2: the run-length posterior P(r_t | x_{1:t})
shifts toward small r when changepoints happen. But the strict
P(r_t = 0 | x_{1:t}) is structurally equal to the hazard rate H,
because growth and changepoint branches share the same predictive
multiplier. The informative quantity is `P(r_t < k)` for some
small k — "probability that the current run is short" — which
spikes after real changepoints.

Stage 6 uses k = 5. The first 5 timesteps are skipped (warm-up
artifact). Tested by `test_bocpd_changepoint_spikes_at_known_break`.

## 9. BOCPD label inherits from rules baseline

Per prompt §Method 5: BOCPD's value is its `changepoint_probability`
output (stored in `metadata` + `transition_prob` columns). For the
`label` column we delegate to `RulesRegimeClassifier` to ensure the
row carries a usable name. Documented in metadata as
`label_source: "rules.v1"`.

## 10. Refit cadence + Dagster schedule staggering

```
Daily   23:30 UTC  regime_classification_job
                   compute_all_signals_job  (Stage 5 schedule)
Weekly  00:00 UTC  dislocation_models_refit
        01:00 UTC  factor_exposure_models_refit
        02:00 UTC  catalyst_models_refit
        03:00 UTC  nowcasting_models_refit
        04:00 UTC  regime_refit_job  (GMM weekly; HMM/MS-VAR
                                       included_quarterly=True)
        05:00 UTC  regime_attribution_job
```

Stage 6 keeps the quarterly HMM/MS-VAR cadence by *not* triggering
separate quarterly jobs — the weekly refit calls
`run_weekly_refit(include_quarterly=True)`. Production can split
into separate quarterly schedules when the runtime cost becomes
prohibitive (HMM converges fast on the small panel; MS-VAR can
take 30-60 s).

## 11. Attribution: 252-day lookback, ≥20 observations per cell

Per the prompt's defaults: `lookback_window_days = 252`,
`min_observations_per_cell = 20`. Cells with fewer than 20
(date, instrument) observations don't get persisted — Stage 7
will see them as missing and weight the signal lower for that
regime.

`position_return = sign(signal.raw_value) * next_log_return`;
Sharpe annualised by √252. Hit rate is the fraction of
(date, instrument) pairs where the signal sign matched the next-
day return sign.

## 12. `hmmlearn` added as core dep (not gated extra)

Unlike EconML (~200MB) which lives behind the `[ml]` extra,
`hmmlearn` is a small (~5MB) C-extension package. Adding it to
core deps means HMM "just works" on every install. Stage 6
still wraps HMM with `_hmmlearn_available()` so a stripped
production image without hmmlearn doesn't crash registration —
the method silently skips and logs.

## 13. Phase 0.1 (Vitest) + 0.2 (backfill) still deferred

Same blockers as previous stages: Node not on PATH, no
FRED_API_KEY. Both items carry forward to Stage 7 as the first
items in its Phase 0.
