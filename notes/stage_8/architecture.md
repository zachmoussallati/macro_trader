# Stage 8 — Architecture

Stage 8 is the portfolio construction layer. It turns composite
scores into sized positions through proper risk-budgeted optimisation
+ a 3-level staged drawdown gate. It is the last layer before Stage
9's walk-forward backtester.

## Component map

```
.
├── alembic/versions/0007_portfolio_schema.py
├── config/base.yaml                # portfolio: section (composite extended Stage 7)
├── src/macro_trader/portfolio/
│   ├── __init__.py                 # package docstring
│   ├── covariance/
│   │   ├── __init__.py
│   │   ├── methods.py              # LedoitWolfCovariance + DCCGARCHCovariance
│   │   ├── runner.py               # daily compute
│   │   ├── refit.py                # weekly DCC-GARCH refit
│   │   └── register.py
│   ├── construction/
│   │   ├── __init__.py
│   │   ├── methods.py              # 4 portfolio methods + ConstraintConfig
│   │   ├── comparator.py           # PortfolioConstructionComparator
│   │   ├── runner.py               # daily compute
│   │   └── register.py
│   └── drawdown/
│       ├── __init__.py
│       ├── detector.py             # equity-curve + rolling-window math
│       ├── gates.py                # evaluate_gate() state machine
│       └── runner.py               # daily realised-return -> equity row -> state update
├── src/macro_trader/db/models/portfolio.py    # 5 ORM models
├── src/macro_trader/methods/setup.py          # registers Stage 8 components
├── orchestration/assets/portfolio.py          # 3 Dagster assets
├── orchestration/definitions.py               # 4 jobs + 4 schedules
├── api/routers/portfolio.py                   # 8 REST endpoints
├── frontend/src/pages/Portfolio.tsx           # /portfolio page
├── frontend/src/pages/Home.tsx                # top positions + gate card
├── frontend/src/api/client.ts                 # 9 Stage 8 typed shapes
└── tests/
    ├── integration/composite/test_composite_pipeline.py  (Phase 0.1)
    ├── unit/portfolio/test_drawdown_gates.py             (Phase 0.2)
    └── unit/portfolio/test_construction_methods.py       (Phase 2)
```

## Data flow

```
       composite.linear.v1 daily 23:45
                  │
                  ▼
       signals.composite_scores
                  │
                  ▼  (instrument-keyed scores + confidence)
                  │
       Ledoit-Wolf daily 23:50
                  │
                  ▼
       portfolio.covariance_estimates + volatility_estimates
                  │
                  ▼
   ┌──────────────┴───────────────┐
   │ drawdown gate                │
   │   compute_realised_return    │
   │   evaluate_gate              │
   │   -> portfolio.drawdown_state │
   │   -> portfolio.equity_curve   │
   └──────────────┬───────────────┘
                  │
                  ▼  (gate scaling factor)
                  │
   4 portfolio methods (daily 00:05 UTC next day)
   ┌──────────────────┐    ┌──────────────────┐
   │ ERC (BASELINE)   │    │ HRP (SHADOW)     │
   └──────────────────┘    └──────────────────┘
   ┌──────────────────┐    ┌──────────────────┐
   │ BL  (SHADOW)     │    │ CVaR (SHADOW)    │
   └──────────────────┘    └──────────────────┘
                  │
                  ▼
       portfolio.positions  (per-method)
                  │
                  ├──► /portfolio UI
                  ├──► /portfolio/risk
                  └──► (Stage 9: walk-forward backtester)
```

## Schema (alembic 0007)

```
portfolio.covariance_estimates   (hypertable on as_of)
  PK (method_id, as_of, instrument_a, instrument_b)
  covariance, correlation, lookback_days, cov_metadata
  diagonal entries hold variance (a == b); off-diagonals hold cov

portfolio.volatility_estimates   (hypertable on as_of)
  PK (method_id, as_of, instrument_id)
  volatility (annualised), lookback_days, vol_metadata

portfolio.positions              (hypertable on as_of)
  PK (method_id, as_of, instrument_id)
  target_weight (post-gate), pre_gate_weight,
  expected_vol_contribution, composite_score, block,
  gate_level, gate_scaling_factor, position_metadata,
  lineage_id

portfolio.equity_curve           (hypertable on as_of)
  PK (method_id, as_of)
  nav, daily_return, cumulative_return, peak_nav,
  drawdown_from_peak, equity_metadata

portfolio.drawdown_state         (state row per method)
  PK (method_id)
  current_gate_level, level_{1,2,3}_triggered_at,
  level_{1,2}_release_at, effective_scaling_factor,
  updated_at, drawdown_metadata
```

`pre_gate_weight` + `target_weight` together let the dashboard show
"the optimiser wanted X; the gate scaled it to Y."
`composite_metadata.expected_portfolio_vol` carries the pre-scaling
realised vol so we can verify vol-targeting was tight.

## The six methods

| method_id                       | component                | status | refit cadence                  |
| ------------------------------- | ------------------------ | ------ | ------------------------------ |
| `covariance.ledoit_wolf.v1`     | covariance_estimate      | BASELINE | stateless; daily recompute    |
| `covariance.dcc_garch.v1`       | covariance_estimate      | SHADOW | weekly Sunday 07:30 UTC       |
| `portfolio.erc.v1`              | portfolio_construction   | BASELINE | stateless                    |
| `portfolio.hrp.v1`              | portfolio_construction   | SHADOW | stateless                    |
| `portfolio.black_litterman.v1`  | portfolio_construction   | SHADOW | stateless                    |
| `portfolio.cvar.v1`             | portfolio_construction   | SHADOW | stateless                    |

All four portfolio methods consume the same `PortfolioInput` and
emit the same `PortfolioOutput` (one `PortfolioPosition` per
instrument). Only the *allocation* step differs.

## Sign convention (enforced uniformly)

`sign(target_weight) == sign(composite_score)` across all 4
methods. Composite tells the system which way to face; the
portfolio method decides magnitude.

ERC and HRP ignore composite *magnitude* (they're risk-driven).
BL uses composite × annualised vol as view returns. CVaR derives
its target expected return from the average composite × vol.

Sign is enforced *after* the optimiser solves, so a BL posterior
flip on a marginal name doesn't override the upstream signal.

## Drawdown gate (independent of method)

Three staged thresholds:

| Level | Trigger | Effect | Release |
| --- | --- | --- | --- |
| 1 | daily return <= -5% | 0.5x scaling for 3 days | auto |
| 2 | trailing 5-day <= -8% | 0.3x scaling for 10 days | auto |
| 3 | peak drawdown <= -10% | 0.0x positions | manual |

Gates compound multiplicatively: level_1 + level_2 active → 0.15x.
Level 3 zeroes regardless of other levels.

`portfolio/drawdown/gates.py:evaluate_gate()` is a pure function:
takes the prior state + today's (daily, rolling_5d, peak) inputs
and returns the new state. No mutation; all DB IO lives in
`runner.py`.

The runner's order of operations:

1. Compute realised return (yesterday's positions × today's
   close / yesterday's close per instrument).
2. Append the equity curve row (running peak + drawdown_from_peak).
3. Read trailing 5-day returns from equity_curve.
4. Read prior state from drawdown_state.
5. Evaluate gate; persist new state.

Only the production portfolio method (`portfolio.erc.v1` by
default) drives the gate state. All 4 methods read the same
scaling factor and apply it uniformly — operators cannot
smart-size around a gate by switching methods.

## Block-aware constraints

Block map (from `Stage 4A` instrument reseed via `asset_class`):

- `energy`: CL, BZ, NG, HO, RB
- `base_metals`: HG, ALI
- `precious_metals`: GC, SI, PL
- `grains`: ZC, ZS, ZW

Per-method constraints (`ConstraintConfig`):

- `target_portfolio_vol = 0.12` (12% annualised)
- `max_block_weight = 0.40`
- `max_position_weight = 0.15`
- `min_positions = 3`
- `min_composite_score_threshold = 0.10`

Two-layer enforcement: in-optimiser inequality constraints (ERC's
SLSQP; CVaR's LP rows) + post-optimiser iterative share-based
clipper (`_apply_position_and_block_caps`). Iteration converges in
<= 5 steps practically.

## Comparator

`PortfolioConstructionComparator`. Key metrics:

- `position_sign_agreement` — load-bearing; methods should agree
  on direction 95%+ since they share composite signs by design.
- `top_3_long_overlap` / `top_3_short_overlap` — do the methods
  agree on the biggest positions? Lower thresholds (60%) — these
  are sensitive to magnitude weighting.
- `weight_correlation` / `rank_correlation` — pearson + spearman
  on signed weights.
- `weight_l1_distance` — total magnitude difference.
- `expected_vol_a/b` — each method's reported portfolio vol.

## Dagster cadence after Stage 8

```
Daily   23:30 UTC  compute_all_signals + regime_classification
        23:45 UTC  composite_score                 (Stage 7)
        23:50 UTC  covariance_estimates            (NEW)

Daily+1 00:05 UTC  portfolio_positions             (NEW)

Weekly  Sunday  00:00  dislocation_refit
                01:00  factor_exposure_refit
                02:00  catalyst_refit
                03:00  nowcasting_refit
                04:00  regime_refit
                05:00  regime_attribution
                06:00  composite_weights_refit
                06:30  composite_bayesian_refit
                07:30  covariance_dcc_refit         (NEW)

Quarterly  first Sunday Jan/Apr/Jul/Oct 07:00 UTC  composite_gbm_refit
```

Drawdown state updates happen inside `run_daily_portfolio` (before
the optimiser solves) — no separate Dagster asset is needed.

## API surface

```
GET  /api/v1/portfolio/methods
GET  /api/v1/portfolio/positions?method_id=...&as_of=...
GET  /api/v1/portfolio/positions/history?instrument=...&method_id=...
GET  /api/v1/portfolio/risk?method_id=...&covariance_method_id=...
GET  /api/v1/portfolio/drawdown?method_id=...
POST /api/v1/portfolio/drawdown/release   (admin auth required)
GET  /api/v1/portfolio/equity?method_id=...
GET  /api/v1/portfolio/covariance?method_id=...
```

## Dashboard

- `/portfolio`: method selector + drawdown gate banner + positions
  table (signed weights with pre-gate / post-gate columns) +
  equity curve (30-day tail) + block exposure breakdown + drawdown
  gate state card with manual L3 release button (visible only when
  L3 active).
- `/` (Home): "Today's sized portfolio" card with top 5 long sizes
  + top 5 short sizes + gate badge when non-none.

## Cross-stage dependencies

| Upstream | What portfolio consumes from it |
| --- | --- |
| Stage 2 data quality | clean DailyBar closes for the returns panel |
| Stage 7 composite | composite_scores rows + per-row confidence |
| Stage 4A instrument reseed | asset_class column → block membership |

| Downstream | What it consumes from portfolio |
| --- | --- |
| Stage 9 backtester | positions + equity_curve for walk-forward eval |
| Stage 10 execution | sized positions + drawdown gate state |

## Testing posture

- 27 unit tests on the drawdown detector + gate state machine
  (Phase 0.2).
- 9 unit tests on the 4 construction methods (vol-target,
  block caps, eligibility filter).
- 1 integration test on the composite pipeline (Phase 0.1).
- 5 Vitest cases on `Portfolio.test.tsx` (empty-state coverage).
- 311 unit tests passing total (+36 from Stage 7).

## Lint / type discipline

- Strict ruff across portfolio package + API router + frontend.
- `# ruff: noqa: N806` file-level in `construction/methods.py` +
  `covariance/methods.py` for the stat-ML uppercase Sigma / Q / R
  / D matrix convention.
- mypy: same pre-existing pandas-stubs `import-untyped` warnings
  as Stages 5-7; no new strict-mypy violations introduced.
