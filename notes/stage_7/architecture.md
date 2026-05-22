# Stage 7 — Architecture

Stage 7 is the composite scoring layer. It is the system's first
integration of signals × regimes × the methods framework, and the
last layer before Stage 8 turns scores into sized positions.

## Component map

```
.
├── alembic/versions/0006_composite_scores_and_weights.py
├── config/base.yaml                  # `composite:` + signals.trend.regime_adjustments + designated.composite_score
├── src/macro_trader/composite/
│   ├── __init__.py                   # package docstring
│   ├── transition.py                 # BOCPD changepoint -> conviction multiplier
│   ├── weights.py                    # attribution -> per-(regime, signal) snapshot
│   ├── methods.py                    # 3 composite methods + CompositeInput/Output
│   ├── comparator.py                 # CompositeComparator (top_5_overlap is load-bearing)
│   ├── runner.py                     # daily compute job
│   ├── refit.py                      # weekly weights + Bayesian refit; quarterly GBM
│   └── register.py                   # registers 3 methods with the registry
├── src/macro_trader/db/models/signals.py    # CompositeScore + CompositeWeight ORM
├── src/macro_trader/signals/trend/methods.py  # regime_state wiring (Phase 4)
├── src/macro_trader/signals/trend/runner.py   # passes regime label via SignalInput
├── orchestration/assets/composite.py # 4 Dagster assets
├── orchestration/definitions.py      # 4 jobs + 4 schedules
├── api/routers/composite.py          # 5 REST endpoints
├── frontend/src/pages/Composite.tsx  # /composite page
├── frontend/src/pages/Home.tsx       # top-ideas + regime card
├── frontend/src/api/client.ts        # CompositeMethod/Score/Breakdown/Weights/Transition shapes
└── tests/
    ├── integration/regime/test_regime_pipeline.py        (Phase 0.3)
    ├── integration/regime/test_full_stack_with_regime.py (Phase 0.4)
    ├── unit/composite/test_transition.py
    ├── unit/composite/test_weights.py
    └── unit/signals/trend/test_regime_adjustments.py
```

## Data flow

```
   FRED / yfinance / CFTC / EIA / USDA / Google Trends
                          │
                          ▼
              data quality + lineage
                          │
                          ▼
       10 signal families  ──►  signals.signal_values   ─┐
                          │                              │
                          ▼                              │
       regime classifier  ──►  regime.regime_states  ───┤
       (5 methods)                                       │
                          │                              │
                          ▼                              │
       regime attribution ──►  regime.regime_attribution │
       (weekly)                                          │
                          │                              │
                          ▼                              │
       composite weights ──►  signals.composite_weights  │
       (weekly snapshot)            (snapshot_ts)        │
                          │                              │
                          ▼                              ▼
       composite methods (linear / bayesian_hier / gbm)
                          │
                          ▼
                 signals.composite_scores
                          │
                          ▼
                    /composite UI
                          │
                          ▼
                    (Stage 8: portfolio)
```

## Schema (alembic 0006)

```
signals.composite_scores   (hypertable on value_ts)
  PK (method_id, instrument_id, value_ts, observation_ts)
  raw_score, score, confidence, regime_label,
  n_signals_used, composite_metadata JSONB, lineage_id
  indices on method_id, instrument_id, observation_ts

signals.composite_weights  (snapshot table; not a hypertable)
  PK (method_id, snapshot_ts, regime_label, signal_method_id)
  weight, weight_source, weight_metadata JSONB
  index on (method_id, snapshot_ts) for "latest snapshot" lookups
```

`composite_metadata` JSONB carries:
- `transition_multiplier`, `transition_probability` (linear method)
- `regime_probability_vector`
- `contributions: [{signal_method_id, weight, z, confidence, contribution}]`

`weight_metadata` JSONB carries `{n_observations, raw_weight,
normalised_weight, sharpe}` per (regime, signal) bucket.

## The three methods

| method_id                       | status   | refit cadence                       | fit shape                                                    |
| ------------------------------- | -------- | ----------------------------------- | ------------------------------------------------------------ |
| `composite.linear.v1`           | BASELINE | stateless (weights weekly)          | reads weight snapshot directly                               |
| `composite.bayesian_hier.v1`    | SHADOW   | weekly Sunday 06:30 UTC             | 3-level ridge with shrinkage to global / per-regime priors   |
| `composite.gbm.v1`              | SHADOW   | quarterly first Sunday 07:00 UTC    | LightGBM on signals × regime probs × instrument one-hot      |

All three:
- Consume `CompositeInput(instrument_ids, as_of, start, end,
  regime_method_id, bocpd_method_id, designated_signal_method_ids)`.
- Emit `list[CompositeOutput]` (per (instrument, value_ts)).
- Persist via `signals.composite_scores`.

Linear is BASELINE → designated as production in
`config/base.yaml:signals.designated_per_component.composite_score`.

## Weight derivation (the load-bearing piece)

`composite/weights.py:compute_regime_conditional_weights()`:

1. Pull `regime.regime_attribution` rows for the trailing 252 days,
   filtered to one regime method (default `regime.rules.v1`).
2. Per (regime_label, signal_method_id):
   - If `n_observations >= 20` and sharpe non-null:
     `raw_weight = max(0, sharpe)`.
   - Else: 1.0 prior fallback (`weight_source = "prior"`).
3. Per-regime normalise so weights sum to 1.0 within each regime.
4. EWM smooth with `alpha=0.3` against the prior snapshot
   (`weight_source = "smoothed"` for blended rows).
5. Persist via `persist_weight_snapshot()` (upsert on the 4-column
   PK so re-runs are idempotent).

`effective_weights_for_probabilities(snapshot, prob_vec)` collapses
the (regime, signal) grid into per-signal effective weights:

```
effective_k = sum_r (prob_r × weight_r,k)
```

(Probability-weighted blending, pre-decided in `decisions.md`.)

## Transition dampener

`composite/transition.py:transition_multiplier_from_probability()`:

```
prob < threshold:  1.0
prob in [threshold, 1.0]:  linear from 1.0 down to floor
None / NaN:        1.0 (fail open)
```

Defaults `threshold=0.5`, `floor=0.5`.

Linear method applies explicitly. Bayesian + GBM include the raw
probability in their feature panel and absorb the dampening
implicitly.

## Trend-ensemble regime wiring (Phase 4)

`signals/trend/runner.py:run_daily_trend()`:

```
regime_label = _latest_regime_label(session)   # via resolve_id("regime_classifier")
sig_input = SignalInput(..., regime_state=regime_label)
```

`signals/trend/methods.py:TrendEnsemble.compute()`:

```
weights = _ensemble_weights_for_regime(method_ids, data.regime_state)
return equal_weighted(per_component, weights=weights, regime_state=...)
```

`_ensemble_weights_for_regime()` reads the merged config
(`signals.trend.ensemble_weights` × `signals.trend.regime_adjustments`)
and returns a dict mapping each SMA method_id to its per-regime weight.

Resolves to:

| regime              | short | medium | long |
| ------------------- | ----- | ------ | ---- |
| risk_on_growth      | 0.333 | 0.333  | 0.334|
| risk_off_defensive  | ~0.50 | ~0.33  | ~0.17|
| stagflation         | 0.333 | 0.333  | 0.334|
| carry_friendly      | ~0.17 | ~0.33  | ~0.50|
| vol_spike           | ~0.60 | ~0.30  | ~0.10|

## Dagster cadence after Stage 7

```
Daily   23:30 UTC  compute_all_signals_job + regime_classification_job
        23:45 UTC  composite_score_job              (NEW)

Weekly  Sunday  00:00  dislocation_refit_job
                01:00  factor_exposure_refit_job
                02:00  catalyst_refit_job
                03:00  nowcasting_refit_job
                04:00  regime_refit_job
                05:00  regime_attribution_job
                06:00  composite_weights_refit_job  (NEW)
                06:30  composite_bayesian_refit_job (NEW)

Quarterly  first Sunday Jan/Apr/Jul/Oct 07:00 UTC  composite_gbm_refit_job (NEW)
```

`composite_score_job` depends on every signal asset plus
`regime_classification` so it always reads fresh upstream rows.

## API surface

```
GET /api/v1/composite/methods
GET /api/v1/composite/scores?method_id=...&as_of=...
GET /api/v1/composite/breakdown?instrument=...&method_id=...&as_of=...
GET /api/v1/composite/weights?method_id=...&regime_method_id=...&as_of=...
GET /api/v1/composite/transition_multiplier?as_of=...&threshold=...&floor=...
```

All return Pydantic v2 response models defined in
`api/routers/composite.py`. `_opt_float` mirrors the regime router's
NaN-safe float coercion pattern.

## Dashboard

- `/composite` page: method selector + ranked instrument table
  (click to load breakdown) + per-signal contribution table with
  signed bars + weights heatmap (signal × regime) + effective
  weights summary + regime probability vector.
- `/` (Home): "Top ideas" card (top 5 long + top 5 short from
  composite, fallback to per-instrument heatmap sum) + "Regime +
  conviction" card (label + days_in_regime + transition multiplier
  + cp probability).
- `/signals` heatmap: unchanged at 10 columns. Composite is not a
  signal family.

## Cross-stage dependencies

| Upstream                | What composite consumes from it                   |
| ----------------------- | ------------------------------------------------- |
| Stage 2 data/quality    | clean signal_values via DailyBar + macro series   |
| Stage 3-5 signals       | 10 signal_method_ids per signal family            |
| Stage 4B factor_exposure | factor panel reused as regime feature columns    |
| Stage 6 regime classifier | regime_states.probability_vector + label        |
| Stage 6 regime attribution | regime.regime_attribution rows                  |
| Stage 6 BOCPD            | regime.regime_states.transition_prob              |

| Downstream | What it consumes from composite                              |
| ---------- | ------------------------------------------------------------ |
| Stage 8 portfolio | signals.composite_scores rows (sized via ERC/HRP/BL/CVaR) |
| Stage 9 backtester | composite_scores history for walk-forward Sharpe         |

## Testing posture

- 14 unit tests on weights + transition (`tests/unit/composite/`).
- 11 unit tests on the trend-ensemble regime weight builder
  (`tests/unit/signals/trend/test_regime_adjustments.py`).
- 2 integration tests in `tests/integration/regime/`
  (Phase 0.3/0.4) cover regime pipeline + attribution plumbing.
- The signal-pipeline emission half is exercised per-family by each
  family's dedicated integration test (Stage 3/4A/4B/4C/5/6).
- 5 Vitest cases on `Composite.test.tsx` cover empty-state UI.
  Same pattern as Stage 6's `Regime.test.tsx`.

## Lint / type discipline

- Strict ruff on `src/macro_trader/composite/` + `api/routers/composite.py`.
- `# ruff: noqa: N803, N806` file-level in `methods.py` for the stat-ML
  uppercase X / V / W matrix convention (same as Stage 5 bvar.py +
  Stage 6 regime/methods.py).
- mypy: a handful of pre-existing pandas-stubs `import-untyped`
  warnings remain; same shape as the regime + nowcasting modules
  before us.
