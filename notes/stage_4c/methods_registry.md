# Stage 4C — methods registered

No new method_ids; Stage 4C upgrades two existing EconML-gated
methods from "structurally registered" to "actually computing
CATE".

| method_id | component | status | Stage 4B state | Stage 4C state |
| --- | --- | --- | --- | --- |
| `factor_exposure.causal_forest.v1` | factor_exposure_signal | SHADOW | Implemented but `est.fit()` missing required `X` arg → silently returned empty `_state`. Synthetic test would have caught it. | `est.fit(Y, T, X=W, W=W)` so EconML actually computes per-(instrument, factor) CATE. New synthetic test pins the fix. |
| `catalyst.causal.v1` | catalyst_signal | SHADOW | Placeholder that delegated to `EventStudyCatalyst`; metadata tagged with `placeholder_for_cate=True`. | Real `CausalForestDML` per (instrument, event_subject) pair with ≥15 events; falls back to event-study sensitivity for under-served pairs (`fallback=True` in state). `placeholder_for_cate` flag removed; `fallback_pairs: int` added. |

## Designated-method config (unchanged)

```yaml
signals:
  designated_per_component:
    trend_signal: trend.ensemble.v1
    carry_signal: carry.spot_proxy.v1
    value_signal: value.zscore.v1
    positioning_signal: positioning.cot_zscore.v1
    dislocation_signal: dislocation.pca.v1
    factor_exposure_signal: factor_exposure.ols.v1
    catalyst_signal: catalyst.event_study.v1
```

The CF / causal SHADOWs are eligible for promotion but Stage 9's
backtester is the formal gate.

## Updated promotion criteria (template)

```python
PromotionCriteria(
    component="factor_exposure_signal",
    min_shadow_period_days=180,
    min_comparison_runs=24,
    required_improvements=[
        "sharpe", "max_dd_ratio", "stability",
        "loading_correlation",   # methods see same factor structure
        "value_correlation",
    ],
    improvement_threshold=0.05,
)

PromotionCriteria(
    component="catalyst_signal",
    min_shadow_period_days=180,
    min_comparison_runs=12,
    required_improvements=[
        "sharpe", "max_dd_ratio",
        # Stage 4C: meaningful now that causal isn't a placeholder.
        "sensitivity_correlation",
        "fallback_pairs",   # promotion requires this go down over time
    ],
    improvement_threshold=0.05,
)
```

## Stable-as-of-Stage-4C interfaces

Stage 5+ can rely on:

- All seven signal families' `compute()` returning the standard
  `list[SignalOutput]` schema.
- Per-method `_state` cached in
  `system.methods_registry.serialized_blob` for dislocation,
  factor_exposure (all three), and catalyst (both).
- The `[ml]` extra is the canonical install pivot — `uv sync
  --extra dev` for the lean stack, `uv sync --extra dev --extra ml`
  for the causal methods.
- `tests/integration/fixtures/backfill.py` + the `real_data`
  marker for any future stage that needs real-data validation.
