# Stage 8 → Stage 9 (walk-forward backtester) handoff

Stage 8 produces live position sizes daily. Stage 9 validates them
historically: walk-forward backtest, deflated Sharpe, scenarios,
Monte Carlo stress. The first stage where shadow methods can
honestly be promoted to PRODUCTION based on OOS evidence rather
than just framework metrics.

## What Stage 9 can rely on (stable as of Stage 8)

- `portfolio.positions` populates daily at 00:05 UTC for every
  registered portfolio method. `target_weight` is post-gate;
  `pre_gate_weight` is pre-gate. Both columns available so the
  backtester can replay with or without the gate.
- `portfolio.equity_curve` tracks NAV / cumulative_return /
  peak_nav / drawdown_from_peak per method.
- `portfolio.drawdown_state` is the live gate state; Stage 9 needs
  to *replay* gate logic over historical positions (don't read the
  state directly — re-compute).
- `signals.composite_scores` history goes back to Stage 7's launch
  (~2 weeks live + ~1 year backfill once the user runs the
  composite backfill job for older signal_values).
- All 4 portfolio methods + both covariance methods register and
  produce daily output. The comparator runs daily.

## What Stage 9 should add

### Phase 0 housekeeping

Operational items continuing from Stages 5-8:

- **Vitest CI lane**: 10 `.test.tsx` files exist; need a CI lane
  with Node on PATH. Documented in Stage 8 decisions.md.
- **`real_data` backfill**: `FRED_API_KEY` still required for the
  cache. One local regen unblocks the 2 `real_data` tests.
- **Portfolio pipeline integration test**: ship one in Stage 9
  along the same lines as Stage 8's
  `tests/integration/composite/test_composite_pipeline.py`. Seed
  composite_scores + DailyBar history → run
  `run_daily_covariance` → `run_daily_portfolio` → assert
  positions land + the drawdown gate state is correct.

### Phase 1+ — walk-forward backtester

The Stage 9 prompt should cover:

- **Schema**: `backtest.runs` + `backtest.daily_metrics` +
  `backtest.position_history` (separate from the live
  `portfolio.positions` so backtests don't collide with
  production data).
- **Walk-forward engine**: roll the as_of cursor forward day by
  day; at each step, re-compute composite + covariance + portfolio
  using only data that would have been visible at that as_of;
  persist the resulting position; advance.
- **Realised return computation**: for each day, the realised
  return is `sum_i (yesterday_position_i × today_return_i)`. Use
  the existing `compute_realised_portfolio_return` from
  `drawdown/runner.py`.
- **Replay the gate**: at each step, evaluate the gate state
  from the synthetic equity curve. Don't read the live
  `drawdown_state` — that's a single point-in-time row.
- **Aggregate metrics**: annualised Sharpe, Sortino, max
  drawdown, hit rate, Calmar ratio, deflated Sharpe (per Bailey
  & Lopez de Prado 2014 — Stage 9 must compute the trial-count
  deflation factor honestly).

### Phase 2+ — scenarios + stress

- **Monte Carlo**: bootstrap returns + perturb covariance (e.g.
  bump all cross-instrument correlations by +0.2 in `vol_spike`
  regime); replay; report distribution of final NAV + max DD.
- **Historical scenarios**: 2008 (broad correlation breakdown),
  2014 oil crash (single-block crisis), 2020 COVID (vol-spike
  regime), 2022 inflation surge (regime-rotation). Replay each
  on the historical signal panel; report per-scenario metrics.
- **Sensitivity analysis**: bump each constraint by ±10% (vol
  target, block cap, position cap); replay; report sensitivity
  of Sharpe + max DD to each parameter.

### Phase 3+ — promotion criteria

Stage 9 introduces the first formal promotion gates:

- Composite shadow → PRODUCTION: backtester Sharpe must be `>= linear
  baseline Sharpe + 0.15` on the full 10-year walk-forward,
  AND `top_5_overlap >= 0.6` consistently in the last 90 days.
- Portfolio shadow → PRODUCTION: backtester Sharpe `>= ERC + 0.10`
  AND max DD `<= ERC max DD + 2%` AND `position_sign_agreement >=
  0.95`.
- Covariance shadow → PRODUCTION: portfolio-driven Sharpe (when
  the candidate covariance is used as input) `>= LW-driven
  Sharpe + 0.05` AND fewer drawdown gate triggers per year.

## Open questions for Stage 9's `decisions.md`

1. **Walk-forward cadence**: daily roll or weekly? Daily is
   honest but expensive (10 years × 252 days = 2520 iterations).
   Weekly amortises the cost but misses intra-week dynamics.
2. **Initial NAV**: $100, $1M, or just keep unitless at NAV=1.0?
   Cleanest is unitless; some metrics (e.g. transaction cost
   bps) need a dollar denominator.
3. **Bootstrap method**: i.i.d. resampling vs block bootstrap.
   Block (e.g. 20-day blocks) preserves autocorrelation but
   reduces effective sample size; i.i.d. is honest but throws
   away regime structure. Probably both, side by side.
4. **Multiple testing correction**: deflated Sharpe per Bailey &
   Lopez de Prado is the academic answer. The trial count is 4
   portfolio × 3 composite × 2 covariance = 24 combos plus
   parameter sweeps. The factor will be substantial — be honest
   about it.

## API endpoints expected for Stage 9

| Method | Path                                                       | Purpose                                 |
| ------ | ---------------------------------------------------------- | --------------------------------------- |
| GET    | `/api/v1/backtest/runs`                                    | List historical backtest runs           |
| GET    | `/api/v1/backtest/runs/{run_id}/metrics`                   | Aggregate metrics for one run           |
| GET    | `/api/v1/backtest/runs/{run_id}/equity`                    | Equity curve from a backtest            |
| GET    | `/api/v1/backtest/scenarios`                               | Per-scenario stress results             |
| POST   | `/api/v1/backtest/runs`                                    | Trigger a new backtest run (admin)      |

## Dashboard

- Stage 9 page `/backtest`: run history + per-run equity + per-run
  metrics + scenario comparison.
- Home page card: "Backtester pulse" — last walk-forward Sharpe,
  max DD vs realised over last quarter.

## What's drifting / worth watching

- `portfolio.positions` is a hypertable on `as_of`. Growth rate:
  4 methods × 13 instruments × 365 days/year = ~19k rows/year.
  Tiny — no retention policy needed for v1.
- `portfolio.equity_curve` grows ~4 × 365 = 1460 rows/year per
  method. Also tiny.
- `portfolio.covariance_estimates` grows by ~2 methods × 13² ×
  365 = ~123k rows/year. Comfortable for several years; Stage 13
  monitoring should add a retention policy (e.g. weekly snapshots
  for > 1 year old).
- DCC-GARCH serialised state ~50 KB per snapshot. Fits in the
  registry. The weekly refit overwrites in place.
- 7 new Dagster schedules (after Stage 7's additions and Stage
  8's 4 new ones). Total now ~22 schedules. Each is a tiny cron
  entry; no infra impact.

## Test coverage carried forward

- 14 unit tests on composite weights + transition (Stage 7).
- 11 unit tests on trend regime adjustments (Stage 7).
- 27 unit tests on drawdown gates + detector (Stage 8).
- 9 unit tests on portfolio construction methods (Stage 8).
- 1 integration test on composite pipeline (Stage 8).
- 311 unit tests passing total.
- 10 Vitest files in `frontend/src/test/`.

Stage 9's test sketch:

- Unit: `tests/unit/backtest/test_walk_forward.py`,
  `test_deflated_sharpe.py`, `test_scenario_replay.py`.
- Integration: `tests/integration/backtest/test_backtest_pipeline.py`
  — full 1-year synthetic backtest from seeded composite +
  covariance.
- Vitest: `Backtest.test.tsx` following the established pattern.
