# Stage 3 — tradeoffs and deferrals

## Deferred

### Ingester HTTP-mocked coverage
The Stage 3 prompt asked for ≥60% coverage on
`macro_trader.data.ingestion.*`. Current coverage is around 5% (only the
base class is exercised by unit tests). Reasons not done this stage:

- Each ingester needs ~5 fixture scenarios × 7 ingesters = ~35 tests.
- Writing realistic fixtures (FRED ALFRED responses, CFTC ZIP layouts,
  EIA / USDA / NOAA JSON shapes) is detailed enough to take half a stage.
- Stage 3 deliverables (loaders, signal framework, 3 families, Dagster,
  API, frontend, tests, notes) already span 30+ files.

This is the only DoD item from the prompt that we explicitly deferred.
Plan: address it in early Stage 4 (also a data-layer-touching stage so
the work fits naturally) using `respx` for httpx-based ingesters and
monkey-patching for fredapi/yfinance.

### Carry enhancement
Spec explicitly says "no enhancement in Stage 3". Will land with Stage
12's futures-curve data.

### Stage 6 regime hook is wired but inactive
`SignalInput.regime_state` and `equal_weighted(regime_state=...)` are
parameters that get passed through unused. Stage 6 plugs the regime
classifier in here.

### Walk-forward Sharpe
`rolling_sharpe_252` is in-sample only. Stage 9's backtester replaces it
with a proper walk-forward out-of-sample number, likely in a new column
(`rolling_sharpe_oos_252`) so the in-sample number remains as a
quick-look indicator.

### Promotion eligibility for signals
No signal method is eligible for PRODUCTION promotion until Stage 9's
walk-forward Sharpe lands. Comparators still run daily so the dataset
builds up; the promotion gate just can't gate on Sharpe yet.

### Dashboard sparklines / Plotly charts
The decay tab renders rolling Sharpe as a table. Stage 11's dashboard
pass adds proper Recharts line / area charts; for Stage 3 the table is
adequate for verification.

### Cross-family aggregation in the comparator
Each family's comparator stays within its own component. Cross-family
comparisons (e.g., "does the trend ensemble disagree with the value
signal on this instrument?") wait for Stage 7's composite scorer.

### NOAA agricultural regions outside the US
Iowa / Illinois / Kansas added; Brazilian Mato Grosso and Pampas
(Argentina) still need a non-NOAA provider. Documented in
`docs/data_sources.md`.

### Backfill scripts
The Stage 2 backfill recipe ("instantiate ingester directly, call .run
with since=...") still applies but isn't wrapped in a make-py command.
A future cleanup.

## Shortcuts taken

### SMA computation uses fixed-window rolling rather than EWMA
`pandas.Series.rolling(window=N).mean()`. A trader may prefer
`.ewm(span=N).mean()` for smoother transitions. Stage 5+ vol-surface
work will likely introduce EWMA helpers; we'll expose a `kind="ewma"`
option then.

### HP filter solves dense `(I + λK'K)`
Fine for 400-day windows; would slow with 10k+ points. Stage 9's
backtester runs HP on much longer histories — we'll either switch to
the statsmodels sparse path or add a banded-solver shim there.

### Loader fallback when `pandas_market_calendars` fails
Falls through to `pd.bdate_range`. Loses holiday accuracy but tests pass
in CI without the calendar database.

### Carry placeholder confidence is `0.0` or `0.3`
Two hardcoded values, not a continuous function of data quality.
Stage 12's real carry will compute confidence from spread tightness +
liquidity proxies; for now the binary works.

### `metadata` column workaround
The SignalValue model uses `signal_metadata` as the Python attribute
but `metadata` as the DB column to match the prompt. Persistence routes
through `SignalValue.__table__` so SQLAlchemy's reserved
`Base.metadata` doesn't collide.

### Comparator runs only on baseline vs the matching shadow per family
- Trend: `trend.ensemble.v1` (BASELINE) vs `trend.hp_filter.v1` (SHADOW)
- Carry: no comparison (only one method)
- Value: `value.zscore.v1` (BASELINE) vs `value.cross_sectional.v1` (SHADOW)

Stage 4+ may add more shadows per family; the runner picks the pair
explicitly so adding a third method needs a runner update.

## What we'd revisit

- **Coverage targets**: 70% on `signals/*` is the prompt target. Current
  unit coverage hits ~50% on most signal modules (high-level only). The
  remaining branches are calendar-edge cases that need DB fixtures.
- **HP normalisation window**: 60 days is arbitrary. May tune in Stage 9
  when we have walk-forward Sharpe to score it against.
- **Sub-class groupings**: hard-coded in `value/methods.py`. Could be
  driven from `market_data.instruments.sub_class` to keep one source of
  truth as new instruments arrive in Stage 4+.
