# Stage 6 — usage

## Running the regime classifier manually

```python
from macro_trader.db.engine import get_sessionmaker
from macro_trader.regime.runner import run_daily_regime_classification

sf = get_sessionmaker()
with sf() as s:
    print(run_daily_regime_classification(s))
    s.commit()
```

Or via Dagster: `Assets → regime_classification → Materialize`.

## Refitting the fitted regime methods

```python
from macro_trader.regime.refit import run_weekly_refit

with sf() as s:
    # GMM only (weekly default):
    print(run_weekly_refit(s))
    # Include HMM + MS-VAR (quarterly default):
    print(run_weekly_refit(s, include_quarterly=True))
    s.commit()
```

Dagster: `regime_refit_job` (weekly Sunday 04:00 UTC). The Stage 6
default has `include_quarterly=True` set in the asset, so HMM +
MS-VAR refit weekly too; production can split into a separate
quarterly cron when runtime cost matters.

## Computing attribution

```python
from datetime import datetime
from sqlalchemy import select
from macro_trader.db.models.system import MethodRegistryRow
from macro_trader.regime.attribution import compute_attribution

with sf() as s:
    signal_method_ids = [
        r.method_id
        for r in s.scalars(
            select(MethodRegistryRow).where(
                MethodRegistryRow.component.like("%_signal")
            )
        )
    ]
    for regime_id in ("regime.rules.v1", "regime.gmm.v1"):
        rows = compute_attribution(
            s,
            regime_method_id=regime_id,
            signal_method_ids=signal_method_ids,
            as_of=datetime.utcnow(),
        )
        print(regime_id, len(rows))
    s.commit()
```

Dagster: `regime_attribution_job` (weekly Sunday 05:00 UTC).

## Inspecting regime classifications via API

```bash
# List registered methods
curl http://localhost:8000/api/v1/regime/methods | jq

# Latest regime per method
curl 'http://localhost:8000/api/v1/regime/current?method_id=regime.gmm.v1' | jq

# Per-day label series
curl 'http://localhost:8000/api/v1/regime/history?method_id=regime.rules.v1' | jq

# Attribution heatmap data (rows = signal_method, columns = regime_label)
curl 'http://localhost:8000/api/v1/regime/attribution?regime_method=regime.gmm.v1' | jq

# BOCPD changepoint probabilities
curl 'http://localhost:8000/api/v1/regime/changepoints?from=2026-04-01' | jq
```

## Visit `/regime` in the browser

```
http://localhost:5173/regime
```

Layout:

- **Method selector** — switch between rules / GMM / HMM / MS-VAR /
  BOCPD (only methods registered show up).
- **Current regime card** — name label, probability bar across the
  5 named regimes, days_in_regime + confidence + cp_prob (for BOCPD).
- **Recent history table** — last 60 classified days for the chosen
  method.
- **Attribution heatmap** — rows = signal_method_id, columns = 5
  named regimes, cell = Sharpe coloured emerald (positive) / rose
  (negative). Click cell tooltip for n_observations + hit_rate.

## Interpreting BOCPD changepoint probability

The Stage 6 implementation reports `P(r_t < 5 | x_{1:t})` —
"probability that the current regime run is short". Reads:

- **< 0.10**: regime is well-established (run length probably
  longer than 5 days).
- **0.10–0.30**: weak evidence of a recent transition.
- **> 0.30**: meaningful changepoint signal; combined with the
  rules / GMM label, suggests a regime shift is underway.
- The first 5 timesteps of a fresh window are warm-up — values
  there should be ignored.

## Interpreting attribution

A row like `(regime.gmm.v1, stagflation, trend.ensemble.v1)
sharpe=1.2, hit_rate=0.58, n=24` reads: "across the 24 (date,
instrument) observations where the GMM classified the day as
stagflation, the trend ensemble's directional signal produced a
position with in-sample annualised Sharpe 1.2 and 58% hit rate."

Stage 7 composite scoring will weight signals proportionally to
their per-regime Sharpe.

## When you don't see data

| Empty state | Fix |
| --- | --- |
| `/regime` "no regime methods registered" | Run `methods/setup.py:register_all_methods` (Dagster does this on startup) |
| `/regime` "no regime state yet" | Run `regime_classification` Dagster asset |
| `/regime` "no attribution rows yet" | Run `regime_attribution_compute` Dagster asset (needs regime_states + signal_values populated first) |
| Attribution cells empty | Cell has < 20 observations; regime needs more history |
