# Stage 7 → Stage 8 (portfolio construction) handoff

Stage 7 produces composite scores. Stage 8 turns them into sized
positions: covariance estimation, weight optimisation, drawdown
gates.

## What Stage 8 can rely on (stable as of Stage 7)

- `signals.composite_scores` populates daily at 23:45 UTC for
  every active instrument with at least 5 contributing signals.
  Score is bounded in `[-1, 1]`. Confidence is bounded in `[0, 1]`.
- `signals.designated.resolve_id("composite_score")` returns the
  designated production composite (default `composite.linear.v1`).
  Stage 8's portfolio optimiser reads from this one method only.
- `composite_metadata.regime_probability_vector` and
  `composite_metadata.transition_multiplier` are available per row
  so Stage 8 can condition position sizing on regime certainty.
- `composite_metadata.contributions` carries the per-signal
  decomposition; Stage 8 can compute "signal disagreement" as the
  std of contributions if it wants a richer confidence measure.
- The BOCPD-driven `transition_multiplier` already dampens
  conviction during regime transitions at the score level; Stage
  8 doesn't need to re-apply it (would be double-dampening).

## What Stage 8 should add

### Phase 0 housekeeping (carry-forwards from Stages 5-7)

- **Vitest CI lane**: still deferred. Three stages of Vitest
  files exist and follow the established pattern; we just can't
  run them in this harness. A CI lane with Node on PATH closes
  this for good. Stage 8 Phase 0.1 candidate.
- **Backfill cache for `real_data` tests**: still deferred.
  `tests/integration/fixtures/backfill.py:regenerate()` needs
  `FRED_API_KEY` in the env. One local run unblocks the 2
  `real_data` tests. Stage 8 Phase 0.2 candidate.
- **Composite pipeline integration test**: ship one in Stage 8
  Phase 0 along the same lines as Stage 7's
  `tests/integration/regime/test_regime_pipeline.py`. Seed signal
  panel + regime panel + attribution → run `run_daily_composite()`
  → assert rows land in `signals.composite_scores` for all 3
  methods (with appropriate gating for GBM).

### Phase 1+ — portfolio construction

The Stage 8 prompt should cover at minimum:

- **ERC baseline** `portfolio.erc.v1`: equal risk contribution.
  Volatility estimated via 60-day rolling std of returns; covariance
  via Ledoit-Wolf shrinkage. Position size = composite score sign
  × (target vol / instrument vol) × portfolio risk budget.
- **HRP shadow** `portfolio.hrp.v1`: hierarchical risk parity per
  Lopez de Prado 2016. Uses the same Ledoit-Wolf covariance as
  the ERC baseline but allocates via hierarchical clustering.
- **BL shadow** `portfolio.black_litterman.v1`: Black-Litterman
  posterior with composite scores as the "views" matrix and the
  Ledoit-Wolf covariance as the prior.
- **CVaR shadow** `portfolio.cvar.v1`: minimise the Conditional
  Value at Risk subject to a target expected return derived from
  composite scores.

Persistence: `portfolio.positions` hypertable keyed on (method,
instrument, value_ts). One row per (method, instrument) per day.

Drawdown gates:

- `-5%` daily: position sizes scaled by 0.5 for the next 3 trading
  days.
- `-8%` over 5 days: scaling factor drops to 0.3 for 10 days.
- `-10%` from peak: all positions zero until a manual restart.

These probably live as a separate `portfolio.drawdown` table that
the optimiser reads on each daily run.

## Open questions for Stage 8's `decisions.md`

1. **Covariance estimator**: Ledoit-Wolf shrinkage vs DCC-GARCH.
   LW is faster and simpler; DCC-GARCH captures time-varying
   correlations. Probably ship LW as BASELINE and DCC-GARCH as
   SHADOW.
2. **Position sign**: take from `sign(composite_score)`, or from
   `sign(composite_score * (1 - drawdown_dampener))`? The latter
   lets the dampener flip a marginal long into a flat position;
   the former keeps the dampener purely additive.
3. **Regime-conditional risk budgets**: should the portfolio risk
   budget itself be regime-conditional (e.g. lower in `vol_spike`)?
   The composite already absorbs some of this via the transition
   multiplier; double-application would over-dampen.
4. **Covariance lookback under regime shifts**: rolling 60-day std
   weights every observation equally; an EWM (alpha=0.05) would
   adapt faster to regime shifts. Pick one.

## API endpoints expected for Stage 8

| Method | Path                                                | Purpose                                        |
| ------ | --------------------------------------------------- | ---------------------------------------------- |
| GET    | `/api/v1/portfolio/positions?as_of=...`             | Current sized positions per instrument         |
| GET    | `/api/v1/portfolio/positions/history?instrument=`   | Position history time series                    |
| GET    | `/api/v1/portfolio/risk?as_of=...`                  | Per-instrument vol + portfolio risk usage      |
| GET    | `/api/v1/portfolio/drawdown?as_of=...`              | Current drawdown state + active dampening       |
| GET    | `/api/v1/portfolio/methods`                         | List of portfolio methods + status              |

## Dashboard

- Stage 8 page `/portfolio` (ranked positions table + per-instrument
  risk attribution + drawdown gauge).
- Home page card: "Today's portfolio" — top 5 long sizes + top 5
  short sizes + total portfolio risk usage.

## What's drifting / worth watching

- `signals.composite_weights` snapshot table grows by ~650 rows per
  week (10 signals × 5 regimes × 13 instruments × 1 method per
  snapshot). Stage 13 monitoring should add a retention policy
  (e.g. keep weekly snapshots for 2 years, monthly thereafter).
- `composite_metadata` JSONB on `composite_scores` carries the full
  per-signal contributions array (10 floats × few keys). At ~13
  instruments × 365 days = ~5000 rows per year per method, this is
  manageable but worth watching as we add methods.
- The Bayesian hierarchical method serialises ~650 cell-level
  weight tables. Pickle blob size is ~50 KB uncompressed; with
  zlib it's ~15 KB. Fine for `system.methods_registry.serialized_blob`.
- The GBM method serialises a LightGBM booster; ~200-500 KB
  pickled. Compressed to ~80-150 KB. Also fine but the biggest
  blob in the registry.
- 4 new Dagster schedules; total schedule count after Stage 7 is
  ~18. Each is a tiny cron entry, no infra impact.

## Test coverage carried forward

- 14 unit tests in `tests/unit/composite/` (weights + transition).
- 11 unit tests in `tests/unit/signals/trend/test_regime_adjustments.py`
  (Phase 4 wiring).
- 2 integration tests in `tests/integration/regime/`
  (Phase 0.3/0.4 from Stage 7 prereqs).
- 9 Vitest files in `frontend/src/test/` (still deferred on the
  harness).

Stage 8's test sketch:

- Integration: `tests/integration/composite/test_composite_pipeline.py`
  end-to-end on seeded data (Phase 0 candidate).
- Unit: `tests/unit/portfolio/test_erc.py`, `test_hrp.py`,
  `test_drawdown_gates.py`.
- Vitest: `Portfolio.test.tsx` following the established empty-
  state pattern.
