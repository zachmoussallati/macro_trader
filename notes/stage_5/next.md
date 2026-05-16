# Stage 5 → Stage 6 (regime classifier) handoff

Stage 5 completes the signal stack. Stage 6 turns to the regime
classifier — the input that Stage 7's composite scoring will use
to condition signal weights.

## What Stage 5 finishes (final state of the signal stack)

- **10 signal families** producing daily output:
  trend / carry / value / positioning / dislocation /
  factor_exposure / catalyst / alt_data / nowcasting / vol_surface.
- **`compute_all_signals_job`** materialises all 10 daily assets
  + 4 weekly refit assets (dislocation, factor_exposure,
  catalyst, nowcasting). Vol-surface methods + alt-data methods
  are stateless — no refit assets.
- **Methods registry** has the final 23 method registrations
  across the 10 components (`signals/setup.py:register_all_methods`).
- **Heatmap shows 10 columns**; Stage 4C's checkbox selector
  handles the table density.

## Stage 6 housekeeping (do first)

Three small items inherited from Stage 5's deferrals:

### 6.1 Run the deferred verification

- `cd frontend && pnpm test` — execute the four Stage 4C Vitest
  smoke tests + any new vol_surface / nowcasting / alt_data page
  smoke tests Stage 5B may have added.
- `python -m tests.integration.fixtures.backfill` — regenerate the
  504-day cache (needs `FRED_API_KEY`). Then `pytest -m real_data`
  to validate the Stage 4C + Stage 5 real-data tests.
- `python make.py test` against a live Postgres — confirm the
  Stage 5 alembic 0004 migration runs cleanly and the three new
  families produce rows through the runner pipeline.

### 6.2 Wire options-chain ingestion as a Dagster asset — DONE in follow-up

The follow-up commits after the `stage-5-complete-signal-stack`
tag added `ingest_options_chains` (group `ingest_market_data`,
daily 21:30 UTC schedule). `signal_vol_surface` now takes an
`AssetIn(key="ingest_options_chains")` so the chain ingest is
upstream of the daily signal. Operators can also run the new
`ingest_options_chains_job` for one-off backfills.

### 6.3 Add nowcasting consensus values to calendar ingest

Stage 5 nowcasting's surprise computation falls back to
`(predicted - last_actual)` when consensus is unavailable. Stage 6
or a dedicated calendar-ingest enhancement should pull consensus
from a free aggregator (`econoday.com`) or paid provider
(TradingEconomics) and populate
`calendar_events.metadata.consensus`.

## What Stage 6 can rely on (stable as of Stage 5)

- All 10 signal families' historical outputs in
  `signals.signal_values` — provide the data for regime
  performance attribution (e.g. "trend signals perform best in
  trending regimes; mean-reversion signals in ranging regimes").
- `signals/factor_exposure/factors.py:build_factor_panel` — the
  six macro factors (growth / inflation / liquidity / usd / oil /
  risk_on) are exactly the kind of low-dimensional summary a
  regime classifier should condition on.
- The methods framework's `serialize` / `deserialize` + registry
  blob storage pattern — regime classifier states (HMM transition
  matrices, GMM cluster parameters) cache the same way.
- `Method.metadata.method_id` namespacing convention —
  `regime.hmm.v1`, `regime.gmm.v1`, `regime.rules.v1`, etc.

## Stage 6 — regime classifier

Stage 6's prompt covers:

- **Rules baseline** (`regime.rules.v1`): VIX-bucket +
  yield-curve-slope-bucket + USD-trend rules → categorical regime
  label. No fitting; pure logic.
- **HMM shadow** (`regime.hmm.v1`): hidden Markov model on the
  factor panel.
- **GMM shadow** (`regime.gmm.v1`): Gaussian mixture clustering
  on the factor panel.
- **Markov-Switching VAR shadow** (`regime.msvar.v1`): proper
  switching regression. Heaviest method.
- **BOCPD shadow** (`regime.bocpd.v1`): Bayesian online change-
  point detection for regime transition timing.

Per the methods framework, only the rules baseline is BASELINE
initially; HMM / GMM / MS-VAR / BOCPD are SHADOWs. Promotion
requires Stage 9 backtester results.

## Open questions for Stage 6 `decisions.md`

1. **Regime state-space**: continuous (e.g. probability vector
   over K regimes) vs discrete labels? The prompt suggests
   discrete; the HMM / GMM naturally produce probabilities.
   Surface the probability + assigned label in
   `system.regime_states` so Stage 7 can choose.
2. **Number of regimes**: 3 (risk-on / risk-off / neutral) vs
   5 (add carry-on / vol-spike) vs let the GMM choose? Pick a
   small fixed K to keep dashboards interpretable.
3. **Regime detection lag**: HMM smoothing vs filtering for
   real-time use. Real-time = filtering only.

## Stage 5 deferrals to revisit during Stage 6

- Frontend pages for vol_surface, nowcasting, alt_data: **DONE
  in follow-up** (basic Recharts + table versions). Plotly 3D
  surface viz on `/signals/vol_surface` is still deferred per
  `notes/stage_5/decisions.md` §11.
- 5 API endpoints listed in the Stage 5 prompt's "API Additions"
  block: **DONE in follow-up**
  (`/vol_surface/slices`, `/vol_surface/term_structure`,
  `/nowcasting/projections`, `/nowcasting/history`,
  `/alt_data/components`).
- `ingest_options_chains` Dagster asset: **DONE in follow-up**.
- Full Gatheral SVI (post-paid-data, per `tradeoffs.md` §2): still
  deferred. Spline fallback continues to ship for Stage 5/6.

## What might still change in Stage 6+

- `signals.signal_values.metadata` is starting to look heavy
  (factor_loadings + factor_zscores + cates + chain_freshness +
  releases + ...). Stage 6 should profile query times on a real
  dataset; consider promoting hot metadata fields to columns if
  query latency drifts.
- The Stage 4A `signals.designated.resolve_id` resolver was built
  for 5 components; it now handles 10. No code changes expected
  but worth a smoke test under load.
- Methods page `ShadowDifferentiationBadge` (Stage 4C Phase 2.3)
  shows one badge per component using the most-recent comparator
  row. For factor_exposure with 2 shadows the badge picks
  whichever pair was compared last. Stage 6 frontend pass could
  split into one badge per (baseline, shadow) pair.
