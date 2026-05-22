# Stage 7 — Methods Registry additions

3 new methods join `system.methods_registry` under the
`composite_score` component.

## `composite.linear.v1` — BASELINE

| Field        | Value                                                          |
| ------------ | -------------------------------------------------------------- |
| method_id    | `composite.linear.v1`                                          |
| component    | `composite_score`                                              |
| version      | `1.0.0`                                                        |
| status       | `BASELINE` (designated as production)                          |
| refit        | stateless; weights snapshot weekly                             |
| dependencies | numpy, pandas, sqlalchemy                                      |

**What it does.** For each (instrument, value_ts):

```
raw_score = sum_k (effective_weight_k × z_k × confidence_k)
score = tanh(clip(raw_score, -clip_z, clip_z)) × transition_multiplier
```

`effective_weight_k` comes from the probability-weighted blend of the
latest weight snapshot against the current regime probability vector.

**Parameters** (config `composite.linear`):

- `clip_z: 3.0` — clip raw_score before tanh.
- `min_signals_for_score: 5` — drop rows with fewer non-null signals.
- `transition_dampen_threshold: 0.5` (the `composite.transition.threshold` config).
- `transition_dampen_factor: 0.5` (the `composite.transition.floor` config).

**Output metadata.** `composite_metadata` JSONB carries
`transition_multiplier`, `transition_probability`,
`regime_probability_vector`, and the full `contributions: [...]`
list with `{signal_method_id, weight, z, confidence, contribution}`
per row.

**Strengths.** Interpretable, decomposable, fast. Each signal's
contribution to the score is a single number you can show to an
operator. The dashboard's `/composite` breakdown view depends on
this metadata.

**Weaknesses.** Linear: doesn't capture interactions between signals
(e.g. "trend × low VIX" being multiplicatively bullish). Doesn't
quantify uncertainty.

## `composite.bayesian_hier.v1` — SHADOW

| Field        | Value                                                          |
| ------------ | -------------------------------------------------------------- |
| method_id    | `composite.bayesian_hier.v1`                                   |
| component    | `composite_score`                                              |
| version      | `1.0.0`                                                        |
| status       | `SHADOW`                                                       |
| refit        | weekly Sunday 06:30 UTC                                        |
| dependencies | numpy, pandas, sqlalchemy                                      |

**What it does.** Three-level shrinkage on signal weights:

- **Global prior**: ridge OLS of next-day log return on the wide
  (signals × confidences) panel. Centres the per-regime betas.
- **Per-regime betas**: ridge OLS on the regime-subset panel,
  shrunk toward global by `regime_shrinkage` (default 0.3).
- **Per-(regime, instrument) betas**: ridge OLS on the (regime,
  instrument)-subset panel, shrunk toward per-regime by
  `instrument_shrinkage` (default 0.5). Cells with fewer than
  `max(10, n_features/2)` observations fall back to the per-regime
  prior.

Daily compute uses probability-weighted blending across the per-
(regime, instrument) weight tables to produce a final
`raw_score = sum(blended_weight × z × confidence)`, then tanh-
squashes. BOCPD probability is included in the model's input
panel but not applied as an explicit multiplier (compare with the
linear method).

**Parameters** (config `composite.bayesian_hier`):

- `lookback_days: 504`
- `regime_shrinkage: 0.3`
- `instrument_shrinkage: 0.5`
- `clip_z: 3.0`
- `min_signals_for_score: 5`

**Output metadata.** Includes `regime_probability_vector` and the
full `contributions: [...]` like linear, but the `weight` field for
each contribution is the blended-cell weight from the hierarchy
(not the snapshot weight).

**Strengths.** Captures per-regime and per-instrument idiosyncrasies
in the weight structure. Closed-form posterior means we can compute
credible intervals (deferred to Stage 9).

**Weaknesses.** Single ridge regularisation per level (not a full
hyperprior). MAP weights only — full posterior averaging is in
`tradeoffs.md` as a deferred enhancement.

## `composite.gbm.v1` — SHADOW (gated on lightgbm)

| Field        | Value                                                          |
| ------------ | -------------------------------------------------------------- |
| method_id    | `composite.gbm.v1`                                             |
| component    | `composite_score`                                              |
| version      | `1.0.0`                                                        |
| status       | `SHADOW`                                                       |
| refit        | quarterly first Sunday Jan/Apr/Jul/Oct 07:00 UTC               |
| dependencies | numpy, pandas, sqlalchemy, **lightgbm**                        |

**What it does.** LightGBM regression on the wide feature panel:

- 10 columns: per-signal `z * confidence`.
- 5 columns: `regime_prob_<r>` for each named regime.
- 1 column: `changepoint_probability` (BOCPD).
- 13 columns: instrument one-hots.

Target: next-day log return of the instrument. Train/validation
split is time-ordered (last 20% held out for early stopping).
Daily compute uses the fitted model to predict each row's score,
which is then tanh-squashed and clipped.

**Parameters** (config `composite.gbm`):

- `lookback_days: 1008` (4 years)
- `n_estimators: 500`
- `learning_rate: 0.05`
- `max_depth: 5`
- `min_child_samples: 20`
- `early_stopping_rounds: 30`
- `validation_fold_fraction: 0.2`
- `random_state: 42`

**Output metadata.** `regime_probability_vector`,
`transition_probability`, `n_features` (count of feature columns
the model was trained on). Per-signal contribution decomposition is
not available for GBM (tree-based models don't decompose linearly);
the dashboard's breakdown panel shows "n/a" for GBM rows.

**Strengths.** Captures non-linear interactions between signal,
regime, and instrument that the other methods can't see by
construction. The 4-year training window is enough data to find
genuine non-linearities for the 13-instrument universe.

**Weaknesses.** Opaque vs the linear and Bayesian methods. Quarterly
refit means it adapts slowly to regime distribution shifts. Gated
on the `[ml]` extra dependency (`lightgbm`), which is ~50 MB and
not a core dep.

## Registration

`src/macro_trader/composite/register.py:register(session)`:

```python
register_method(LinearComposite(), MethodStatus.BASELINE, ...)
register_method(BayesianHierarchicalComposite(), MethodStatus.SHADOW, ...)
if _lightgbm_available():
    register_method(GBMComposite(), MethodStatus.SHADOW, ...)
```

Called from `methods/setup.py:register_all_methods()` alongside the
10 signal families and the regime classifier. Idempotent.

## Designation

`config/base.yaml`:

```yaml
signals:
  designated_per_component:
    composite_score: composite.linear.v1
```

`signals.designated.resolve_id("composite_score")` returns
`composite.linear.v1` until an operator overrides it via env-layer
config. The dashboard's default method selector and the daily
runner both honour the designated method as the canonical "what's
shown" choice; comparator runs each shadow against the designated
production method.

## State persistence

`composite.bayesian_hier.v1` and `composite.gbm.v1` are fitted methods;
their state lives in `system.methods_registry.serialized_blob` via
`store_serialized_blob()` / `load_serialized_blob()`. Both compress
with `zlib` when the raw blob exceeds 32 KB, matching the Stage 6
regime-refit pattern.

`composite.linear.v1` has no fitted state — the "training" is the
weekly weight snapshot persisted to `signals.composite_weights`.
