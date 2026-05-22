# Stage 8 — Decisions

Stage 8 turns composite scores into sized positions. Every decision
below is written *before* the implementation it justifies; if the
impl diverges, the divergence is recorded here.

## Phase 0 — Hard prerequisites

### Phase 0.1 — Composite pipeline integration test

`tests/integration/composite/test_composite_pipeline.py` lands.
Seeds 13 instruments + a synthetic signal panel across all 10
designated signal methods + 14 days of regime_states rotating
through all 5 named regimes + attribution rows with enough
observations to drive sharpe-weighted (not flat-prior) weight
derivation. Then asserts:

- `signals.composite_weights` snapshot has per-regime weights
  summing to 1.0 each.
- `composite.linear.v1` produces `signals.composite_scores` rows.
- `composite_metadata.contributions` reconstructs `raw_score`
  within float tolerance (decomposition correctness is load-bearing
  for the `/composite` breakdown view).
- Shadow methods (Bayesian + GBM) are present in the runner output
  even when their fitted state hasn't been hydrated.

Collected cleanly under pytest; skips when Postgres is unreachable.

### Phase 0.2 — Drawdown gate unit tests

Built `portfolio/drawdown/detector.py` + `gates.py` first, then 27
unit tests in `tests/unit/portfolio/test_drawdown_gates.py`:

- Equity-curve math: empty / single positive / single negative /
  multi-day peak tracking / peak_drawdown / rolling_window_return.
- Gate triggers: each level fires at its threshold; gates compound;
  level 3 zeroes out regardless.
- Gate releases: levels 1+2 auto-release after configured days;
  level 3 requires the manual flag.
- Idempotence: re-trigger doesn't extend an active gate;
  evaluate_gate is pure (no mutation of prior state).
- Parameterised truth table over the (daily, rolling, peak) input
  space verifying the full state machine.

All 27 pass; lint clean.

### Operational gaps (user responsibility per .prompts/stage_8_prompt.md)

These remain operational items for the user, not Stage 8 work:

- **Vitest frontend tests**: 10 `.test.tsx` files exist (Methods,
  7 signals pages, Regime, Composite, Portfolio); none have been
  executed in this harness because Node is not on PATH. CI lane
  with Node is the right long-term fix.
- **`real_data` tests**: still gated on `FRED_API_KEY`. One local
  `python -m tests.integration.fixtures.backfill` run unblocks
  them; the cache persists.

## Covariance: Ledoit-Wolf as baseline

Ledoit-Wolf shrinkage is the institutional baseline for good
reason:

- Sample covariance has high variance in small samples and is
  ill-conditioned for matrix inversion (the BL + ERC optimisation
  steps need it stable).
- Shrinkage toward a structured target (constant-correlation by
  default in sklearn) trades a small bias for much lower variance.
- The optimal shrinkage intensity is closed-form (Ledoit-Wolf
  2004) — no hyperparameter to tune.
- Production institutions (e.g. AQR, BlueMountain) use it as their
  default.

DCC-GARCH sits in shadow because:

- Captures regime-dependent correlation structure (correlations
  spike during crises in ways LW misses).
- ~5x heavier to fit (~50s for the 13-instrument universe per
  Engle 2002's recursion); weekly refit cadence chosen for that
  reason.
- Higher variance estimator — needs ~2x more data to be stable
  (504-day lookback vs LW's 252).

Promotion path: 90 days of side-by-side comparator data + a Stage 9
backtester run showing DCC-driven portfolios get better realised
Sharpe in regime-transition windows. Until then, LW stays
production.

## Block + position constraint thresholds

- `max_block_weight = 0.40`: no asset class > 40% of gross
  exposure.
- `max_position_weight = 0.15`: no single instrument > 15% of
  gross.
- `min_positions = 3`: need at least 3 names passing the score
  threshold before any optimisation runs.
- `min_composite_score_threshold = 0.10`: |score| < 0.10 means
  the signal is too weak to be tradeable.

Why these specific numbers:

- 40% block: with 4 blocks (energy/base_metals/precious_metals/
  grains) of unequal size (energy=5, base=2, precious=3, grains=3),
  an equal-weight portfolio puts ~38% in energy (5/13). 40%
  permits the natural energy bias while preventing pure-energy
  portfolios.
- 15% position: 13 instruments × 15% = 195% maximum gross.
  Comfortably above the 100% no-leverage target; lets the
  optimiser concentrate to the natural 8-9% per name when
  diversification suggests it.
- 3 positions minimum: anything less isn't a portfolio; it's a
  concentrated bet.
- 0.10 score threshold: matches `composite.linear.v1`'s
  `min_signals_for_score=5` — both filters say "don't trade noise".

The constraint helper iterates because single-pass capping in
absolute terms then renormalising can leave a block above its
share cap (capping one block raises others' shares). Iteration
converges in <= 5 steps practically.

## Drawdown gate parameters

The -5% / -8% / -10% staged thresholds are calibrated for the
12% annual vol target:

- Daily 12% vol -> ~0.75% daily stdev. A -5% day is ~6.7 sigma,
  i.e. tail event (1-in-thousands by Gaussian; more like 1-in-
  hundreds in practice given fat tails).
- 5-day -8% equates to ~2.4% / sqrt(5) = 1.07 sigma per day for 5
  consecutive days, or one very bad week.
- 10% peak-to-trough is the institutional "this strategy is
  broken" threshold from CTA practice (e.g. Schneeweis et al.
  2002, Kestner 2003).

Scaling factors (0.5x / 0.3x / 0x) match Kestner's "graduated
de-risking" approach: don't go to zero on the first bad day, but
do progressively cut exposure. Auto-release timings (3 / 10 days)
let the system recover when realised vol normalises without
operator intervention.

Level 3 manual restart for the -10% peak drawdown is the
non-negotiable safety brake. Documented in the prompt as "don't
make it convenient to bypass." The API endpoint requires admin
auth; the dashboard surfaces the button only when L3 is active.

## Composite score → position translation

Sign vs magnitude asymmetry per the Stage 8 prompt:

| method | sign source | magnitude source |
| --- | --- | --- |
| ERC | composite | inverse-variance (composite magnitude ignored) |
| HRP | composite | inverse-variance + hierarchical bisection |
| BL | composite (overlay) | mean-variance with composite as view returns |
| CVaR | composite | LP-driven; composite enters via target_expected_return |

ERC and HRP are pure risk-diversification methods: composite
*direction* tells us which way to face, but composite *magnitude*
doesn't help size (a Sharpe-based weighting in BL/CVaR is more
appropriate for using magnitude).

Sign is enforced *after* the optimiser. If BL's mean-variance
posterior wants to flip a sign (e.g. because per-regime view
uncertainty interacts oddly), the post-step composite sign
overlay catches it. This preserves the invariant: every position
has `sign(target_weight) == sign(composite_score)`.

## Sign-preserving vol-target scaling

All four methods scale post-optimisation by
`target_vol / realised_portfolio_vol` to hit the 12% target. This
can push gross above 1.0 — that's deliberate. The system is
allowed to leverage up to whatever the constraints permit. The
gross exposure cap is *implicit* via the per-position and
per-block weight caps (13 × 15% = 195% max).

If gross exceeds operator risk preference, the right knob is
`target_portfolio_vol`, not a hard gross-exposure cap. Stage 9
backtester will verify gross stays inside the implicit cap on
realised data.

## CVaR small-n LP infeasibility

At n=4 with max_position_weight=0.15, the LP equality
`sum(w) = 1` is infeasible (`4 * 0.15 = 0.60 < 1.0`). The method
catches this via `linprog`'s infeasibility status and falls back
to equal-weight inside the eligible set, logging a warning.

Production n=13 doesn't trigger this (`13 * 0.15 = 1.95 ≥ 1`).
A cleaner fix is to relax to `sum(w) <= 1` so the LP can run on
small universes; deferred to `tradeoffs.md` since production
doesn't hit it.

## Block-aware constraint enforcement

Two-layer enforcement:

1. **In the optimiser** (ERC's SLSQP, CVaR's linprog): block-sum
   inequalities passed as `A_ub @ x <= b_ub` rows. The optimiser
   respects them during the solve.
2. **Post-optimisation** (`_apply_position_and_block_caps`):
   iterative share-based cap enforcement catches floating-point
   drift and methods that don't use a constrained optimiser (HRP
   + BL).

The iterative helper exists because single-pass absolute capping
then renormalising can leave a block above its share cap (capping
one block in absolute terms raises others' shares relative to the
new total). Iterating until shares stabilise converges in <= 5
steps; in practice 2-3 iterations suffice for the 4-block
universe.

## Day-rollover schedule (00:05 UTC next day)

The portfolio job runs at 00:05 UTC the *next* day, not 23:55 UTC
the same day. Rationale:

- Composite scoring runs at 23:45 UTC. Covariance estimates at
  23:50 UTC.
- A 10-minute buffer gives both upstream jobs time to complete,
  Dagster materialisation to register, and any retry windows to
  fire.
- Running positions at 23:55 UTC inside the same day creates a
  risk that composite/covariance hadn't materialised yet (Dagster
  doesn't *guarantee* upstream ordering for cron schedules; only
  for asset dependencies).

The day-rollover also matches institutional practice: positions
are "for tomorrow," not "for today." The 5-minute UTC offset
keeps the trading-day rollover clean for instruments listed in
different timezones (no edge case at midnight US Eastern, etc.).
