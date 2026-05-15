# Stage 4A — methods registered

Four new methods registered via `methods/setup.py:register_all_methods`.

| method_id | component | status | role | rationale |
| --- | --- | --- | --- | --- |
| `positioning.cot_zscore.v1` | `positioning_signal` | BASELINE | designated production | Disaggregated COT managed-money net z-score is the conventional baseline in the literature (Briese 2007). Three-year window, 1-year min-history ramp-up. |
| `positioning.cot_commercial.v1` | `positioning_signal` | SHADOW | candidate enhancement | Legacy commercial extremes as a smart-money proxy. Reads from a different report; the methods agree most of the time but diverge at structural extremes — exactly when positioning signals matter most. |
| `dislocation.pca.v1` | `dislocation_signal` | BASELINE | designated production | Static-loadings PCA on a 252-day return panel. Robust, fast, well-understood. |
| `dislocation.dfm.v1` | `dislocation_signal` | SHADOW | candidate enhancement | Kalman-filtered time-varying loadings via statsmodels DynamicFactor. Captures regime-dependent shifts PCA misses; convergence-flaky, hence shadow. |

## Promotion criteria (template, pending Stage 9 backtester)

Stage 9's industrial backtester is the first place comparison
results can be turned into a defensible promotion call. Until then,
the placeholders below are recorded for transparency:

```python
PromotionCriteria(
    component="positioning_signal",
    min_shadow_period_days=180,
    min_comparison_runs=24,        # weekly comparisons -> ~6 months
    required_improvements=[
        "sharpe", "max_dd_ratio", "stability", "extreme_overlap",
    ],
    improvement_threshold=0.05,
)

PromotionCriteria(
    component="dislocation_signal",
    min_shadow_period_days=180,
    min_comparison_runs=12,
    required_improvements=[
        "sharpe", "max_dd_ratio", "stability",
        "explained_variance",        # DFM should explain more than PCA
        "loading_stability",         # DFM loadings should be smoother
    ],
    improvement_threshold=0.05,
)
```

Both criteria objects are NOT instantiated in code yet — they live as
documentation in this file. Stage 7+ will wire them up alongside the
composite scoring promotion gate.

## Designated-method resolution (Stage 4A)

Configured in `config/base.yaml`:

```yaml
signals:
  designated_per_component:
    trend_signal: trend.ensemble.v1
    carry_signal: carry.spot_proxy.v1
    value_signal: value.zscore.v1
    positioning_signal: positioning.cot_zscore.v1
    dislocation_signal: dislocation.pca.v1
```

These are the methods whose values drive the dashboard heatmap /
`/api/v1/signals/heatmap`. The resolution order
(`config -> PRODUCTION -> BASELINE -> first-registered`) means the
heatmap stays sane during method promotions:

- If `dislocation.dfm.v1` is promoted to PRODUCTION, the heatmap
  switches to it automatically — the config override entry is then
  redundant but harmless.
- If an operator wants to override that and keep showing PCA, they
  edit the YAML override and reload.

## Stable-as-of-Stage-4A interface

Stage 4B+ can rely on:

- `MethodMetadata.method_id` and `component` strings — never
  rename without bumping the version suffix.
- The signal output schema (`signals.signal_values`) — Stage 4A
  added rows but no columns.
- `run_comparisons_for_component` — accepts any number of shadows
  per component, so Stage 4B's macro-factor-exposure family (with
  RF + Causal Forest shadows against OLS baseline) wires in with
  zero changes to the comparator runner.
- `signals.designated.resolve` — config-override-first; new
  components can be pinned via YAML without touching code.
