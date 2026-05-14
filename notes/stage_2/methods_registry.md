# Stage 2 — methods registered

Two methods, both for the `data_quality` component. Registered via
`src/macro_trader/data/quality/register.py`, which is called by
`methods/setup.py` on Dagster code-location startup.

## `data_quality.zscore.v1` — BASELINE

- **What**: rolling |z| > 3 over a 60 trading-day window per series.
- **Component**: `data_quality`.
- **Status**: `BASELINE` — this is the driving method for now.
- **Source**: `src/macro_trader/data/quality/methods.py:ZScoreOutlier`.
- **Why baseline**: simplest defensible outlier detector. Easy to reason
  about. Deterministic. No state to refit. Low risk of subtle bugs at
  the cost of low recall on regime changes.
- **Parameters**: `threshold=3.0`, `window=60` (configurable via
  `config.data_quality.zscore_threshold` and `zscore_window` — currently
  hardcoded on the method; YAML wiring to come in Stage 3).
- **Failure mode**: misses long-tail outliers that are within ±3σ of a
  drifting mean. Flags excess noise during the first `window-1`
  observations of a series (configurable cold-start).

## `data_quality.isoforest.v1` — SHADOW

- **What**: sklearn `IsolationForest` per series, `contamination=0.01`.
- **Component**: `data_quality`.
- **Status**: `SHADOW` — runs in parallel, does not drive decisions.
- **Source**: `src/macro_trader/data/quality/methods.py:IsolationForestOutlier`.
- **Why shadow**: a tree-based density estimator catches non-Gaussian
  structure (regime breaks, fat-tail clusters) that z-score misses. The
  cost is stochasticity (`random_state` fixed at 42 for reproducibility),
  refit time (~50 ms per series per day), and a less interpretable
  prediction surface.
- **Parameters**: `contamination=0.01`, `random_state=42`,
  `n_estimators=100`.
- **Refit policy**: refit every run from the same input series (no
  cross-day state persistence). For Stage 2 the input window is ~365
  trading days from `_load_close_series` in `runner.py`.
- **Failure mode**: cold start (<10 valid observations) returns
  all-False; sensitive to `contamination` choice.

## Comparator

`DataQualityComparator` (`src/macro_trader/data/quality/comparator.py`)
emits:

| Key | Meaning |
| --- | --- |
| `flag_rate_a` | fraction of inputs flagged by baseline |
| `flag_rate_b` | fraction of inputs flagged by shadow |
| `agreement_iou` | intersection-over-union of the two bool masks |
| `agreement_correlation` | Pearson correlation on the 0/1 vectors |
| `n_observations` | input length |

The comparator runs daily via `run_daily_quality_check` in the
`daily_data_quality` Dagster asset. One row per (instrument) per day
lands in `system.method_comparisons` with `component = "data_quality"`.

## Promotion criteria (proposed)

```python
PromotionCriteria(
    component="data_quality",
    min_shadow_period_days=180,
    min_comparison_runs=120,        # ~10 instruments × 12 monthly comparisons
    required_improvements=["agreement_iou"],
    improvement_threshold=0.05,     # 5% better
)
```

`evaluate_promotion` looks for keys `agreement_iou_a` and
`agreement_iou_b` in each `metrics` dict. Our current comparator
emits `agreement_iou` (not split by method). To use this criterion
without hand-labelled ground truth we'd either:

1. Add a synthetic injected-outlier benchmark (inject 1% known outliers,
   measure each method's recall, store as `recall_{a,b}`); promotion
   would then gate on `recall` improvement at fixed `flag_rate`.
2. Or define a domain-specific oracle (e.g. flag rows where the
   day-on-day return exceeds a 95th-percentile threshold of the prior 2y
   distribution as a "known-bad" set) and score against that.

Both are Stage 3 work, but the framework is ready to consume the metric
once defined. The operator promotion path is documented in
`docs/runbook.md` and `notes/stage_2/usage.md`.

## Status lifecycle

```
DEVELOPMENT ───► BASELINE (data_quality.zscore.v1)
                    │
                    │ (after 180d + criteria met)
                    ▼
                 (no PRODUCTION yet)
DEVELOPMENT ───► SHADOW (data_quality.isoforest.v1)
                    │
                    │ admin POST /methods/.../status
                    ▼
                 PRODUCTION → demotes zscore to DEPRECATED
```

## API access

```bash
# List both methods (no auth needed for read):
curl http://localhost:8000/api/v1/methods?component=data_quality | jq

# History for one method:
curl http://localhost:8000/api/v1/methods/data_quality.zscore.v1/history | jq

# Comparison rows for the component:
curl "http://localhost:8000/api/v1/methods/comparisons?component=data_quality" | jq
```

## Dashboard view

`/methods` filters to component `data_quality` and shows both rows.
Comparison detail per row will arrive in Stage 11 (full dashboard).
