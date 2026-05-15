# Stage 4A — usage

How to drive the new pieces end-to-end. Assumes you've run
`python make.py setup` so Docker, the test DB, the Python venv, and
the frontend deps are all in place.

## Running the daily signal pipelines

```bash
# Backend + Dagster + frontend in one terminal each (or use `dev-all`):
python make.py dev-dagster

# In the Dagster web UI (http://localhost:3000), the following assets
# now exist:
#   group=signals_positioning   -> signal_positioning
#   group=signals_dislocation   -> signal_dislocation
# plus the existing signals_trend / signals_carry / signals_value.

# Materialise everything with the bundled job:
#   Jobs -> compute_all_signals_job -> Materialize
```

The default schedule runs `compute_all_signals_job` daily at 23:30
UTC, half an hour after `data_quality_job` finishes.

## Inspecting positioning signals

After the positioning asset has run:

```bash
# COT breakdown for a single instrument
curl 'http://localhost:8000/api/v1/signals/positioning/cot?instrument=CL&report_type=disaggregated'

# Latest signal values (per-instrument)
curl 'http://localhost:8000/api/v1/signals/values?instrument=CL'

# Filter to one signal id
curl 'http://localhost:8000/api/v1/signals/positioning.cot_zscore.v1/values?instrument=CL'
```

The heatmap (`/api/v1/signals/heatmap`) now returns 5 columns
(`trend_signal`, `carry_signal`, `value_signal`,
`positioning_signal`, `dislocation_signal`) — the dashboard's
`Signals.tsx` renders them in the order listed.

## Inspecting dislocation signals

```bash
# Per-instrument latest dislocation snapshot for the PCA baseline
curl 'http://localhost:8000/api/v1/signals/dislocation/factors?method_id=dislocation.pca.v1'

# Same for the DFM shadow (may be empty if convergence failed today)
curl 'http://localhost:8000/api/v1/signals/dislocation/factors?method_id=dislocation.dfm.v1'
```

The returned `explained_variance` field tells you how much of the
panel's cross-sectional variance the top-K factors absorbed on the
most recent fit. Below ~0.4 is a signal-quality concern.

## Manually re-running a single family

Bypass Dagster for a one-off run:

```python
from macro_trader.db.engine import get_sessionmaker
from macro_trader.signals.positioning.runner import run_daily_positioning
from macro_trader.signals.dislocation.runner import run_daily_dislocation
from macro_trader.methods.setup import register_all_methods

sf = get_sessionmaker()
with sf() as session:
    register_all_methods(session)
    print(run_daily_positioning(session))
    print(run_daily_dislocation(session))
    session.commit()
```

`run_daily_positioning` and `run_daily_dislocation` both accept an
`instruments=` kwarg if you want to restrict the universe (e.g. to
sanity-check a single commodity).

## Investigating a positioning signal flag

When the Methods page surfaces a `positioning.cot_zscore.v1` row
with extreme metadata (`is_extreme=true`), the workflow is:

1. **Read the underlying COT row** via
   `/api/v1/signals/positioning/cot?instrument=<X>&report_type=disaggregated`.
   Eyeball `managed_money_net = long - short` against the trailing
   weeks: is the latest print truly a multi-year extreme, or did the
   z-score window happen to be unusually narrow?

2. **Cross-check the legacy report** via the same endpoint with
   `report_type=legacy`. If `producer_net` is also at an extreme in
   the OPPOSITE direction, the shadow's commercial-extreme signal
   confirms the call.

3. **Look at the comparator row** for that day in
   `/api/v1/methods/comparisons?component=positioning_signal` — the
   `extreme_overlap` metric tells you how often the two methods agree
   on flagging extremes historically (high overlap = high
   confidence; low overlap = methodology disagreement worth a manual
   read).

## Investigating a dislocation residual

A surprising `dislocation.pca.v1` raw_value at the tail (|raw| > 0.9)
might be:

- **A real fundamental dislocation** — the instrument decoupled from
  its peer factor on news. Check Stage 10's daily brief +
  calendar for the relevant catalyst.
- **A fit artefact** — one instrument's data was sparse during the
  fit window. Check the `dislocation_signal.metadata.explained_variance`
  on the row: if it's low, the residual is noisy by definition.
- **DFM/PCA disagreement** — diff the two via
  `/api/v1/methods/comparisons?component=dislocation_signal`. The
  `residual_correlation` between PCA and DFM tells you whether the
  two methodologies see the same picture.

## Refitting dislocation models manually (future)

Stage 4A re-fits both methods on every daily run, so there is no
manual refit step. The follow-up weekly-refit asset is documented
in `tradeoffs.md`; once it lands, the usage pattern will be:

```bash
# From Dagster UI:
#   Jobs -> dislocation_models_refit_job -> Materialize
# or via Python:
from macro_trader.signals.dislocation.refit import run_weekly_refit
with sf() as session:
    run_weekly_refit(session)
    session.commit()
```

## Running the new tests

```bash
# Just the positioning / dislocation tests:
python make.py test -- tests/unit/signals/positioning/ \
                       tests/unit/signals/dislocation/ \
                       tests/integration/signals/test_positioning_pipeline.py

# Ingester coverage report:
python make.py test -- tests/unit/data/ingestion/ \
    --cov=src/macro_trader/data/ingestion \
    --cov-report=term-missing
```
