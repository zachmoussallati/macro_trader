# Stage 4B — methods registered

Five new methods registered through `methods/setup.py:register_all_methods`.

| method_id | component | status | role | rationale |
| --- | --- | --- | --- | --- |
| `factor_exposure.ols.v1` | `factor_exposure_signal` | BASELINE | designated production | Closed-form, fast (<1s for 13 instruments), interpretable betas, R² as confidence. |
| `factor_exposure.rf.v1` | `factor_exposure_signal` | SHADOW | candidate enhancement | Captures non-linear interactions (copper x DXY conditional on inflation regime). |
| `factor_exposure.causal_forest.v1` | `factor_exposure_signal` | SHADOW (gated on EconML) | candidate enhancement | Properly identified conditional exposures via DML; expensive fit. |
| `catalyst.event_study.v1` | `catalyst_signal` | BASELINE | designated production | Empirical event-study sensitivity per (instrument, subject); 5-year lookback. |
| `catalyst.causal.v1` | `catalyst_signal` | SHADOW (gated on EconML) | candidate enhancement | Stage-4B placeholder delegates to the baseline; real CATE deferred to Stage 4C/9. |

## Designated-method config (post-Stage-4B)

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

The dashboard heatmap reads each component's designated method via
`signals.designated.resolve_id(component)` (Stage 4A, Phase 1b).

## Promotion criteria (template, pending Stage 9 backtester)

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
        "sensitivity_correlation",  # CATE matches event-study where overlap exists
        "events_covered",           # CATE doesn't drop too many subjects
    ],
    improvement_threshold=0.05,
)
```

These objects are documentation only — they're not yet instantiated
in code. Stage 7+ will wire them up alongside composite scoring's
promotion gate.

## Stable-as-of-Stage-4B interfaces

Stage 5+ can rely on:

- All seven signal families' `compute()` returning `list[SignalOutput]`
  with the standard schema (instrument_id, value_ts, raw_value, etc.).
- `Method.serialize()` / `deserialize(blob)` round-trip via pickle for
  every method; the registry's `store_serialized_blob` /
  `load_serialized_blob` helpers wrap the persistence + lookup.
- `signals.factor_exposure.factors.build_factor_panel` /
  `latest_factor_zscores` as the canonical macro factor pipeline.
  Stage 5's nowcasting + vol-surface signals can reuse the same
  factor panel.
- `signals.catalyst.events.upcoming_score` as the canonical
  forward-event aggregator. Stage 6's regime classifier can sign-
  multiply the score to produce a directional signal.
