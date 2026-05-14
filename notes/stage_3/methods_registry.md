# Stage 3 — methods registered

Eight new methods across three signal families. Combined with Stage 2's
two data-quality methods, the registry holds 10 methods total at the
end of Stage 3.

## Trend family (5 methods)

### `trend.sma_short.v1` — BASELINE

- 10/30 SMA crossover on log-prices, tanh-squashed.
- Reacts to ~2-week trend changes.
- Confidence = fraction of non-NaN observations in trailing 252d window.

### `trend.sma_medium.v1` — BASELINE

- 20/60 SMA crossover. Mid-term horizon (~2-3 months).

### `trend.sma_long.v1` — BASELINE

- 50/200 SMA "golden cross" — classical long-term trend.

### `trend.ensemble.v1` — BASELINE (designated production trend signal)

- Equal-weighted average of the three SMA baselines.
- `regime_state` parameter wired through but ignored in Stage 3
  (activated when Stage 6 lands).
- Downstream consumers (Stage 7 composite) should reach for this
  method by id, not via `production_for("trend_signal")`.

### `trend.hp_filter.v1` — SHADOW

- Cycle component of Hodrick-Prescott filter on log-prices
  (lambda=1600), normalised by rolling 60-day std and tanh-squashed.

## Carry family (1 method, placeholder)

### `carry.spot_proxy.v1` — BASELINE

- Placeholder until Stage 12 brings futures-curve data.
- For CL/BZ/GC/NG it emits a small drift-derived proxy with
  `confidence=0.3`.
- For other instruments it emits `raw_value=0` with `confidence=0` so
  composite scoring effectively ignores it.

## Value family (2 methods)

### `value.zscore.v1` — BASELINE

- 252-day rolling z-score of log-prices, sign-inverted (positive=cheap).
- Cross-sectional rank computed over the full universe.

### `value.cross_sectional.v1` — SHADOW

- Same underlying z-score, but ranked within asset sub-class
  (energy / base_metals / precious_metals / agriculture).
- Removes sector-wide drift before ranking.

## Why all-BASELINE for the trend family

The registry's PRODUCTION status is reserved for promoted enhancements.
None of the SMA methods is an enhancement over the others — they're
different horizons. The ensemble is the canonical "production trend"
output that downstream consumers should use, but it's still a baseline
in the framework sense (we haven't yet learned that a shadow does
better).

`production_for("trend_signal")` with multiple BASELINEs returns the
first one enumerated — not a useful default. Stage 7 composite scoring
will reference the ensemble by method_id directly.

## Promotion eligibility deferred

The Stage 3 prompt confirms: signal promotion eligibility waits for the
Stage 9 backtester, which provides walk-forward out-of-sample Sharpe.
Comparator runs are still recorded daily so the promotion dataset
builds up; we just can't gate on Sharpe until then.

Once Stage 9 lands:

```python
PromotionCriteria(
    component="trend_signal",
    min_shadow_period_days=180,
    min_comparison_runs=120,
    required_improvements=["rolling_sharpe_oos"],  # to be added
    improvement_threshold=0.05,
)
```

## API access

```bash
# All signal methods grouped by component:
curl "http://localhost:8000/api/v1/signals" | jq

# Status history for one method:
curl "http://localhost:8000/api/v1/methods/trend.hp_filter.v1/history" | jq

# Comparison runs for a component:
curl "http://localhost:8000/api/v1/signals/comparisons?component=trend_signal" | jq
```
