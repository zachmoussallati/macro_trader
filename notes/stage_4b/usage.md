# Stage 4B — usage

How to drive the new pieces end-to-end.

## Installing the optional `[ml]` extra

The Causal Forest factor exposure method and Causal Catalyst
sensitivity method require EconML. Install via:

```bash
uv sync --extra ml      # adds econml>=0.15.0 (~200 MB transitive deps)
```

Without the extra, both causal methods are silently skipped at
registration / refit / runner time. The OLS / RF factor exposure
methods + EventStudyCatalyst keep producing signals — the family
just runs with one fewer shadow.

CI that wants the causal paths exercised must install with
`--extra ml` for the test step.

## Refitting the model caches manually

```bash
# From Dagster UI (http://localhost:3000):
#   Jobs -> dislocation_refit_job          -> Materialize
#   Jobs -> factor_exposure_refit_job      -> Materialize
#   Jobs -> catalyst_refit_job             -> Materialize

# Or via Python:
from macro_trader.db.engine import get_sessionmaker
from macro_trader.signals.dislocation.refit import run_weekly_refit as refit_disloc
from macro_trader.signals.factor_exposure.refit import (
    run_weekly_refit as refit_factor,
)
from macro_trader.signals.catalyst.refit import run_weekly_refit as refit_catalyst

sf = get_sessionmaker()
with sf() as session:
    print(refit_disloc(session))
    print(refit_factor(session))
    print(refit_catalyst(session))
    session.commit()
```

Each `run_weekly_refit` returns a list of `RefitResult`-style
dataclasses with `blob_size_bytes`, `fit_rows`, etc. Useful for
ad-hoc sanity-checking after universe expansion or a bad week.

## Inspecting factor exposure

```bash
# Latest macro factor z-scores (six factors; missing = no DFII2/VIX yet)
curl 'http://localhost:8000/api/v1/signals/factor_exposure/factors'

# Per-instrument factor loadings + signal snapshot for OLS baseline
curl 'http://localhost:8000/api/v1/signals/factor_exposure/loadings?method_id=factor_exposure.ols.v1'

# Same for RF / Causal Forest (CF only present if [ml] installed)
curl 'http://localhost:8000/api/v1/signals/factor_exposure/loadings?method_id=factor_exposure.rf.v1'
curl 'http://localhost:8000/api/v1/signals/factor_exposure/loadings?method_id=factor_exposure.causal_forest.v1'
```

The `factor_loadings` field in each row is a `dict[factor_name,
beta_or_importance_or_cate]` — the dashboard's factor heatmap reads
this directly.

## Investigating a factor exposure flag

When the methods page surfaces a `factor_exposure.ols.v1` row with
an unusual raw_value (|raw| > 0.7), the workflow:

1. Read the factor z-scores via
   `/api/v1/signals/factor_exposure/factors`. Which factor is most
   extreme today?
2. Read the per-instrument loadings via
   `/api/v1/signals/factor_exposure/loadings?method_id=
   factor_exposure.ols.v1`. Is the instrument's beta to the
   extreme factor large?
3. Check the comparator row in
   `/api/v1/methods/comparisons?component=factor_exposure_signal`
   to see whether RF / Causal Forest agree. Three-way agreement
   means the factor view is broad-based; OLS-only flag means a
   linear artefact worth investigating.

## Inspecting catalyst signals

```bash
# Upcoming events the catalyst signal will fold into per-instrument
# scores in the next 10 days
curl 'http://localhost:8000/api/v1/signals/catalyst/events'

# Per-instrument signal values
curl 'http://localhost:8000/api/v1/signals/catalyst.event_study.v1/values'
```

Each `signal_values` row carries `metadata.n_subjects` (how many
distinct event subjects the sensitivity model has data for that
instrument) — low values flag low-confidence forward scores.

## Investigating a catalyst flag

When the catalyst pressure signal surfaces a high raw_value, the
workflow:

1. Pull the upcoming events:
   `/api/v1/signals/catalyst/events?days_ahead=10` and filter for
   the affected instrument. Which events are within the window?
2. Use `/api/v1/methods/comparisons?component=catalyst_signal` to
   compare the event-study and causal methods. (Note: until Stage
   4C, both produce the same output — `value_correlation_a_b` will
   be 1.0.)
3. Confidence: `metadata.n_subjects` shows how many event subjects
   the historical model could estimate sensitivities for that
   instrument. Below ~5 means the forward score is dominated by a
   small number of historical events and should be discounted.

## Sub-class column inspection

```python
from macro_trader.data.instruments import get_class_groups
from macro_trader.db.engine import get_sessionmaker

sf = get_sessionmaker()
with sf() as s:
    print(get_class_groups(s, ["CL", "BZ", "NG", "HO", "RB", "HG", "ALI"], column="sub_class"))
    # {'crude_oil': ['CL', 'BZ'], 'refined_products': ['NG', 'HO', 'RB'],
    #  'base_metals': ['HG', 'ALI']}
```

## Running the new tests

```bash
# Just the new families:
python make.py test -- tests/unit/signals/factor_exposure/ \
                       tests/unit/signals/catalyst/ \
                       tests/unit/signals/dislocation/test_refit.py \
                       tests/integration/signals/test_dislocation_refit.py \
                       tests/integration/signals/test_value_class_column.py

# Cover the EconML-gated path explicitly:
uv sync --extra ml
python make.py test -- tests/unit/signals/factor_exposure/test_causal_forest.py \
                       tests/unit/signals/catalyst/test_methods.py
```
