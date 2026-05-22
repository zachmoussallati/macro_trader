# Stage 7 — Usage

How to run, query, and interpret the composite scoring layer.

## Running composite scoring locally

```bash
# 1. Ensure Stage 6 has populated regime + attribution.
uv run dagster job execute -f orchestration/definitions.py \
  -j regime_classification_job
uv run dagster job execute -f orchestration/definitions.py \
  -j regime_attribution_job

# 2. Build the weight snapshot from attribution.
uv run dagster job execute -f orchestration/definitions.py \
  -j composite_weights_refit_job

# 3. Fit the Bayesian hierarchical composite (one-off, then weekly).
uv run dagster job execute -f orchestration/definitions.py \
  -j composite_bayesian_refit_job

# 4. (Optional, needs lightgbm) Fit GBM composite.
uv run dagster job execute -f orchestration/definitions.py \
  -j composite_gbm_refit_job

# 5. Run the daily composite scoring.
uv run dagster job execute -f orchestration/definitions.py \
  -j composite_score_job
```

In production Dagster runs these on the cron schedule above
without manual intervention.

## Querying scores

```bash
# Per-instrument scores from the production composite (descending).
curl http://localhost:8000/api/v1/composite/scores

# Same, for the Bayesian shadow.
curl 'http://localhost:8000/api/v1/composite/scores?method_id=composite.bayesian_hier.v1'

# Per-signal breakdown for one instrument.
curl 'http://localhost:8000/api/v1/composite/breakdown?instrument=CL'

# Current weights snapshot (per-regime grid + effective blend).
curl http://localhost:8000/api/v1/composite/weights

# Current conviction multiplier.
curl http://localhost:8000/api/v1/composite/transition_multiplier
```

The `/composite` dashboard page wraps these into a single UI.

## Interpreting top-long / top-short outputs

A composite score is bounded in `[-1, 1]`:

- `score > 0.5`: strong long bias. The combined evidence (10 signal
  families, weighted by per-(regime, signal) historical Sharpe) leans
  positive with enough conviction that the tanh squash is close to
  saturation.
- `score in (0.1, 0.5]`: moderate long bias.
- `score in (-0.1, 0.1)`: noise / no view.
- `score in [-0.5, -0.1)`: moderate short bias.
- `score < -0.5`: strong short bias.

Things to check when interpreting:

1. **Confidence**. A score of 0.6 with confidence 0.3 means only
   ~30% of the contributing signals are speaking with high
   conviction. Treat it as weaker than a 0.4 score with 0.9
   confidence.
2. **n_signals_used**. Composite requires `>= min_signals_for_score`
   (default 5) non-null signals; rows with fewer are dropped from
   the output entirely. If a row reports n_signals_used = 5 exactly,
   you're at the minimum and have less signal diversity than the
   13-instrument median.
3. **transition_multiplier**. If the BOCPD changepoint probability
   is above the threshold, the score has been dampened. The
   `breakdown` endpoint surfaces both the raw score and the
   multiplier so you can see the un-dampened conviction.
4. **regime_label**. The score was generated under that regime's
   weight blend. A signal that's normally weak in `risk_off_defensive`
   shows up small here; the next regime flip could swing the score
   sharply.

## When to switch designated method

Default: `composite.linear.v1` is BASELINE → designated as the
production composite via
`signals.designated_per_component.composite_score`.

To promote `composite.bayesian_hier.v1` or `composite.gbm.v1`:

```yaml
# config/<env>.yaml
signals:
  designated_per_component:
    composite_score: composite.bayesian_hier.v1
```

What to check before promoting:

- `top_5_overlap` between the candidate and current production
  over the trailing 90 days. If it's <70% the candidate is making
  materially different calls; investigate whether those differences
  are because the candidate has access to extra information (e.g.
  better non-linear interaction handling) or because it's
  miscalibrated.
- `value_correlation`. Should be high (>0.85) for two methods
  consuming the same upstream signals; lower than that suggests a
  weight-derivation difference worth understanding.
- `direction_agreement`. Anything <90% with the production method
  suggests the candidate is flipping the sign on a non-trivial
  fraction of (instrument, day) rows. Investigate.
- `confidence`. The candidate should be confidence-matched or
  better than the current production.

The `CompositeComparator` runs daily and persists metrics to
`system.method_comparisons` (queryable via the existing
`/signals/comparisons?component=composite_score` endpoint).

## Regenerating weights manually

```python
from datetime import datetime
from macro_trader.composite.refit import refit_weights
from macro_trader.db.engine import get_session

with get_session() as session:
    n = refit_weights(session)
    print(f"persisted {n} weight rows")
    session.commit()
```

Or via Dagster:

```bash
uv run dagster job execute -f orchestration/definitions.py \
  -j composite_weights_refit_job
```

Snapshots are versioned by `snapshot_ts`; re-running with the same
`as_of` is an upsert on the (method, snapshot_ts, regime, signal) PK.

## Inspecting per-signal contributions

```python
from datetime import datetime, timedelta
import requests

r = requests.get(
    "http://localhost:8000/api/v1/composite/breakdown",
    params={"instrument": "CL", "method_id": "composite.linear.v1"},
)
body = r.json()
print(f"score={body['score']:.3f} (raw {body['raw_score']:.3f})")
print(f"regime={body['regime_label']} multiplier={body['transition_multiplier']:.2f}")
for c in body["contributions"]:
    print(f"  {c['signal_method_id']:<32} w={c['weight']:.3f}  z={c['z']:+.2f}  -> {c['contribution']:+.3f}")
```

This is the same view the `/composite` page shows for the selected
instrument.

## Trend ensemble regime adjustments

The trend ensemble's `regime_state` parameter (no-op since Stage 3)
is now live. Per-regime SMA-horizon weights are in
`config/base.yaml:signals.trend.regime_adjustments`. To override
per environment:

```yaml
# config/prod.yaml
signals:
  trend:
    regime_adjustments:
      vol_spike:
        short: 2.0     # extra emphasis on short SMA in vol spikes
        medium: 0.5
        long: 0.1
```

The runner pulls the latest regime label from the production
regime classifier and passes it to the ensemble each daily run.

## Troubleshooting

### "No composite scores yet" on /composite

Run `composite_score_job` (daily 23:45 UTC). Requires regime
classification + signals to have populated first.

### "No weight snapshot yet" on /composite

Run `composite_weights_refit_job` (weekly Sunday 06:00 UTC).
Requires regime attribution to have populated first.

### Bayesian composite returns []

Check `system.methods_registry.serialized_blob` for
`composite.bayesian_hier.v1`. If empty: the weekly refit hasn't
run (or had no input data). Trigger
`composite_bayesian_refit_job` manually.

### GBM composite returns []

Same as Bayesian, but for `composite.gbm.v1`. Additionally requires
`lightgbm` installed (`uv sync --extra ml`). If lightgbm is missing
the method silently doesn't register.

### Transition multiplier = 1.0 always

BOCPD method `regime.bocpd.v1` hasn't produced a non-null
`transition_prob`. Verify regime_classification has run and
`regime.regime_states` has a recent row with
`method_id = 'regime.bocpd.v1'`.
