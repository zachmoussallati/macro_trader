# Stage 6 — tradeoffs and deferred work

## 1. Full multivariate MS-VAR — univariate fallback shipped

- **What**: Stage 6 uses Markov-switching regression on the first
  principal component of the feature panel, not a full multivariate
  MS-VAR per Krolzig 1997.
- **Why deferred**: statsmodels' multivariate MS-VAR support is
  limited and convergence is fragile on the 10-feature panel.
  The PC1 univariate is a clean compromise — it captures the
  dominant common factor's regime structure and uses the existing
  `MarkovRegression` machinery.
- **Stage 7+ pickup**: if regime quality is bottlenecking
  composite scoring, switch to a true multivariate MS-VAR via
  custom EM (Hamilton 1990) — ~300 lines of numpy on top of the
  existing infrastructure.

## 2. Continuous regime embedding deferred

- **What**: Stage 6 outputs discrete labels (with probability
  vector preserved). Continuous regime embeddings (e.g. a latent
  position on a 2D risk-on/risk-off × growth/inflation plane)
  would give Stage 7 finer-grained weighting.
- **Why deferred**: K=5 named regimes are interpretable and the
  Stage 7 attribution table maps cleanly onto them. A continuous
  embedding would need a different attribution shape (regression
  weights, not per-label buckets).
- **Stage 7+ pickup**: could add a `regime.embedding.v1` method
  later that outputs (factor1_z, factor2_z) coordinates and
  Stage 7 composite uses a Gaussian kernel over them.

## 3. K = 5 fixed

- The prompt pins K = 5. We didn't try K = 3 (simpler) or K = 7
  (more discriminative). Each named regime gets at least one
  centroid; doubling K would split regimes — useful but complex
  to label-anchor.

## 4. Real-time BOCPD alerting deferred

- **What**: BOCPD outputs `changepoint_probability` per day. The
  config's `bocpd.threshold_for_alert: 0.7` is read but not
  wired into a Dagster sensor that would page on threshold
  breach.
- **Why deferred**: alerting infrastructure (Slack / PagerDuty
  webhook, dedupe, oncall mapping) is Stage 13 (Monitoring +
  Polish) territory.
- **Stage 13 pickup**: write a Dagster sensor that polls the
  latest BOCPD row daily; if `changepoint_probability > 0.7`,
  emit an alert.

## 5. HMM smoothing vs filtering disambiguation

- **What**: `hmmlearn.predict_proba` returns smoothed
  probabilities. For the "today" row this looks like filtering
  (no future to back-propagate), but for historical rows it
  uses future information — INVALID for backtests.
- **Mitigation**: the runner persists outputs at one
  `observation_ts` per run. Stage 9 backtester reading the
  attribution table will see smoothed-historical regime
  assignments. For the strict point-in-time backtest, Stage 9
  needs to re-classify history one-step-at-a-time using filtering
  only.
- **Stage 9 pickup**: write `hmm_filter_only()` helper that
  returns alpha-only forward probabilities for any given
  truncation; use this in Stage 9 backfill mode.

## 6. Attribution uses in-sample Sharpe

- **What**: The attribution table's Sharpe is computed on the
  same data window the regime classifier saw. This is fine for
  ranking signals within regimes (and that's what Stage 7 needs)
  but it's a biased estimator of the true out-of-sample Sharpe.
- **Stage 9 pickup**: Stage 9's walk-forward backtester
  recomputes attribution OOS and stores it alongside the
  in-sample version.

## 7. Phase 0.1 (Vitest) + 0.2 (backfill) still deferred

Same as Stage 5: Node not on PATH; FRED_API_KEY missing in
shell. The infrastructure exists; user runs `pnpm test` + the
backfill regenerate script locally to close these.

## 8. No integration test for the regime daily runner

The unit tests cover labeling math + per-method classification on
synthetic data. A full integration test that seeds factor data
into Postgres, runs `regime_classification`, asserts rows land in
`regime.regime_states`, etc. is deferred to Stage 7 alongside the
other Phase 0 catch-ups.

## 9. Regime attribution depends on signal_values being populated

The attribution table is only meaningful once `signals.signal_values`
has weeks of history. On a fresh install, attribution.compute will
return empty rows for every (regime, signal) cell. The Methods page
+ /regime page render zero-data states cleanly.

## 10. Plotly heatmap NOT used on /regime

The attribution heatmap on `/regime` uses a plain HTML table with
Tailwind colour classes (`bg-emerald-500/40`, `bg-rose-500/40`).
Same chart-library policy as Stage 5 (Recharts-only). For the
diverging Sharpe heatmap a table works fine because the data is
naturally tabular (signal × regime), not a continuous 2D surface
where Plotly would shine.
