# Stage 3 — usage

## Running signals

Daily Dagster schedule runs at 23:30 UTC. Manual triggers:

```powershell
# All three families end-to-end
$env:DAGSTER_HOME = "$PWD\dagster_home"
uv run dagster job execute -j compute_all_signals_job -f orchestration/definitions.py

# Or a single family asset from the Dagster UI:
# http://localhost:3000 -> signal_trend / signal_carry / signal_value
```

The runners call `register_all_methods` lazily if the registry is empty
(safety net for fresh DBs); production runs rely on the Dagster
code-location startup hook.

## Inspecting outputs

```bash
# All signal values for one instrument:
curl "http://localhost:8000/api/v1/signals/values?instrument=CL" | jq

# Latest cell per (component, instrument):
curl http://localhost:8000/api/v1/signals/heatmap | jq

# Time series for one signal:
curl "http://localhost:8000/api/v1/signals/trend.ensemble.v1/values?instrument=CL&limit=30" | jq

# Rolling Sharpe decay:
curl "http://localhost:8000/api/v1/signals/trend.ensemble.v1/decay" | jq

# Comparator runs:
curl "http://localhost:8000/api/v1/signals/comparisons?component=trend_signal" | jq
```

Dashboard pages:

- `/signals/heatmap` — the 13×3 matrix.
- `/signals/detail` — per-instrument signal values across components.
- `/signals/decay` — rolling Sharpe over time per signal.
- Home `Top signals` card — cross-component composite ranking.

## Backfill signal history

Each runner takes a `window_days` parameter. To compute the last 90 days
of signals for one family:

```python
from macro_trader.signals.trend.runner import run_daily_trend
from macro_trader.db.engine import get_session

with get_session() as session:
    written = run_daily_trend(session, window_days=90)
    print(written)
```

Repeat for `run_daily_carry`, `run_daily_value`. UPSERT semantics on
`(signal_id, instrument_id, value_ts, observation_ts)` make it safe to
re-run.

## Investigate a single signal value

```sql
-- Latest value per signal for CL, with vintage filter:
SELECT signal_id, value_ts, raw_value, zscore, rank, confidence
FROM signals.signal_values
WHERE instrument_id = 'CL'
  AND observation_ts <= now()
ORDER BY signal_id, value_ts DESC;
```

## Compare baseline vs shadow

```bash
# Recent comparator runs for trend:
curl "http://localhost:8000/api/v1/signals/comparisons?component=trend_signal&limit=10" | jq '.[].metrics'
```

Look for:

- `direction_agreement` — fraction of instruments where baseline and
  shadow point the same way. Below 0.5 means the methods disagree more
  often than not.
- `rank_correlation_a_b` — Spearman correlation on cross-sectional
  ranks. Below 0.3 means the methods are picking different winners.
- `rolling_sharpe_a` / `rolling_sharpe_b` — the in-sample Sharpe of
  each. Until Stage 9 these are not promotion-grade.

## Add a new signal method

```python
# src/macro_trader/signals/<component>/methods.py
from macro_trader.signals.base import SignalInput, SignalMethod, SignalOutput
from macro_trader.methods.base import MethodMetadata

class MyNewSignal(SignalMethod):
    metadata = MethodMetadata(
        method_id="<component>.<name>.v1",
        component="<component>_signal",
        name="...",
        version="1.0.0",
        description="...",
        references=[],
    )

    def compute(self, data: SignalInput, session) -> list[SignalOutput]:
        ...
```

Register it from `<component>/register.py`. The runner picks it up
automatically if it's in `default_<component>_methods()`.

## Stage-4 prep checklist

Before Stage 4 starts, run:

```powershell
python make.py test
python make.py lint
uv run alembic check
```

All three should be clean. If a new signal method has been added and
`alembic check` fails, generate a migration:

```powershell
uv run alembic revision --autogenerate -m "stage 3 followup"
```
