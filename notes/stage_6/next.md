# Stage 6 → Stage 7 (signal combination) handoff

Stage 6 completes the regime classifier — the conditioning variable
Stage 7's composite scoring will use to weight signals.

## What Stage 7 can rely on (stable as of Stage 6)

- All 10 signal families produce daily rows in
  `signals.signal_values`.
- All 5 regime methods produce daily rows in
  `regime.regime_states`, each row carrying both the categorical
  label and the full probability vector across the 5 named regimes.
- `regime.regime_attribution` populates weekly with per-(regime,
  signal) Sharpe + hit_rate. This is the canonical input Stage 7's
  weighting layer reads.
- The methods framework's
  `signals.designated.resolve_id(component)` already returns
  `regime.rules.v1` for `regime_classifier`. Stage 7 calls this
  helper to pick the production regime classifier and join
  attribution against its outputs.
- BOCPD `transition_prob` available as a separate "regime
  uncertainty" channel — Stage 7 could downweight all signal
  conviction when changepoint probability is high.

## What Stage 7 should add

### Phase 0 housekeeping (carry-forwards)

- **Vitest run**: still deferred (Node not on PATH in this
  shell). Stage 7's Phase 0.1 is `cd frontend && pnpm test`
  + fix anything broken. The 7 frontend test files (Methods +
  4 Stage 4C signal pages + 3 Stage 5 signal pages + Regime)
  should all pass given they follow the same pattern.
- **Backfill cache regen**: still deferred. Stage 7's Phase 0.2
  is `python -m tests.integration.fixtures.backfill` once
  `FRED_API_KEY` is available.
- **Integration test for the regime daily runner**: ship one
  in Stage 7 Phase 0 (`test_regime_pipeline.py` seeding a
  factor panel → running `regime_classification` → asserting
  rows land in `regime_states` for all 5 methods).
- **Full multivariate MS-VAR**: optional Stage 7 upgrade if
  regime quality bottlenecks composite weighting. The univariate
  PC1 fallback works but loses cross-feature dynamics
  information.

### Phase 1+ — signal combination

The Stage 7 prompt should cover:

- **Linear baseline** `composite.linear.v1`: simple weighted sum of
  z-scored signal values per instrument, weights from attribution
  table.
- **Bayesian hierarchical shadow** `composite.bayesian_hier.v1`:
  multi-level prior across instruments + signal families with
  regime-conditional weights.
- **GBM shadow** `composite.gbm.v1`: gradient-boosted regression
  predicting next-day return from the signal panel + regime
  one-hot.

Persistence: probably `signals.composite_scores` table (or extend
`signal_values` with a composite `signal_id` namespace).

Stage 7 will be the first system layer to produce actionable
position size recommendations (size = composite_score × portfolio
risk budget). Stage 8 then takes composite scores as input to
proper portfolio construction (ERC, HRP, BL, CVaR, etc.).

## Open questions for Stage 7's `decisions.md`

1. **Composite score sign convention**: keep the long-bias
   convention (positive = long) for consistency, or switch to
   z-scored relative ranking? Long-bias makes Stage 8 cleaner.
2. **Regime conditioning shape**: hard-switching weights per
   regime (Stage 6 supports this via probability vector
   argmax) vs smooth probability-weighted blending. Latter is
   more theoretically clean but harder to interpret.
3. **Confidence aggregation**: weighted by per-signal confidence,
   or treated separately? Signal × regime weights × per-row
   confidence is 3 multiplicative factors; pick a normalisation.
4. **Recency bias in attribution**: Stage 6 uses a 252-day
   trailing window. Should Stage 7 weight recent attribution
   more heavily (exponential decay) given regimes change?

## API endpoints expected for Stage 7

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/composite/scores?as_of=...` | Per-instrument composite scores |
| GET | `/api/v1/composite/breakdown?instrument=...&as_of=...` | Per-signal contribution to the composite |
| GET | `/api/v1/composite/weights?method_id=...&as_of=...` | Current effective per-signal weights |

## Dashboard

- Stage 7 page `/composite` (per-instrument composite score
  ranking + per-signal breakdown bar chart).
- Home page card: "today's top long ideas" + "today's top short
  ideas" sourced from composite scores.

## What's drifting / worth watching

- `regime.regime_states.state_metadata` JSONB: small per-method
  payload now, will grow if methods add more diagnostics.
- HMM smoothing vs filtering disambiguation must be resolved
  before Stage 9 backtester runs (per `tradeoffs.md` §5).
- BOCPD changepoint threshold (`bocpd.threshold_for_alert: 0.7`)
  is config-only; Stage 13 (Monitoring) will turn this into a
  Dagster sensor.
- Heatmap is at 10 columns; Stage 7 adds a `composite` column for
  11. Then Stage 8 portfolio adds nothing visible (it consumes
  composite, doesn't produce a new heatmap entry).
