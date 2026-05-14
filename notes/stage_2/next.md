# Stage 2 → Stage 3 handoff

What Stage 3 (Signal Library Part 1 — trend, carry, value) needs to know.

## Stage 3 scope (reminder)

Three foundational signal families on the 13-commodity universe:

- **Trend**: moving-average crossovers, momentum, MACD, etc. Direct
  feature: latest close vs N-day SMA.
- **Carry**: term-structure carry from the futures curve (when we get
  futures); ETF proxy carry is a degenerate case (use FX carry or
  dividend yield as a proxy).
- **Value**: deviation from a long-run mean, relative-strength
  cross-section, mean-reversion z-score.

Each signal will register itself as a method through the framework.
Many will have a baseline + enhancement pair (e.g., simple SMA vs HP
filter for trend).

## Conventions Stage 3 should follow

### Signal module structure (mirroring Stage 2's data/quality)

```
src/macro_trader/signals/
├── __init__.py
├── trend/
│   ├── __init__.py
│   ├── methods.py        # SMACrossover (BASELINE), HodrickPrescott (SHADOW)
│   ├── comparator.py     # TrendSignalComparator
│   ├── runner.py         # daily run logic
│   └── register.py       # registers methods via framework
├── carry/
│   └── (same shape)
└── value/
    └── (same shape)
```

Each component's `register.py` is added to `methods/setup.py`'s
`register_all_methods`.

### Signal output schema

A signal method returns a `dict[str, float]` keyed by `instrument_id`,
plus optional confidence / metadata. Persist to a new table:

```
signals.signal_values
  signal_id       TEXT FK -> methods_registry.method_id
  instrument_id   TEXT FK -> market_data.instruments
  value_ts        TIMESTAMPTZ
  observation_ts  TIMESTAMPTZ          -- when computed
  value           NUMERIC
  confidence      NUMERIC NULL
  lineage_id      UUID
  PK (signal_id, instrument_id, value_ts, observation_ts)
```

Hypertable on `value_ts`. Aligns with the Stage 2 point-in-time pattern.

### Reading market data correctly (POINT-IN-TIME)

```python
from sqlalchemy import select, or_
from macro_trader.db.models.market_data import DailyBar

def bars_as_of(session, instrument_id, start, end, as_of):
    """Return latest-vintage bars in [start, end), seen-by as_of."""
    return list(session.scalars(
        select(DailyBar)
        .where(DailyBar.instrument_id == instrument_id)
        .where(DailyBar.value_ts >= start)
        .where(DailyBar.value_ts < end)
        .where(DailyBar.observation_ts <= as_of)
        .order_by(DailyBar.value_ts, DailyBar.observation_ts.desc())
    ))
```

Reading macro data:

```python
def series_as_of(session, series_id, value_ts, as_of):
    return session.execute(
        select(SeriesObservation.value)
        .where(SeriesObservation.series_id == series_id)
        .where(SeriesObservation.value_ts == value_ts)
        .where(or_(
            SeriesObservation.realtime_start.is_(None),
            SeriesObservation.realtime_start <= as_of,
        ))
        .where(or_(
            SeriesObservation.realtime_end.is_(None),
            SeriesObservation.realtime_end >= as_of,
        ))
    ).scalar_one_or_none()
```

Every signal must use this pattern — never `SELECT … ORDER BY value_ts
DESC LIMIT 1` without an `as_of` filter.

### Calendar integration

Use `is_blackout` to mask catalyst windows:

```python
from macro_trader.calendar.api import is_blackout

if is_blackout(session, instrument_id=symbol, ts=trading_date):
    signal_value = None  # or last-known carry-forward
```

For catalyst-sensitivity signals (Stage 4), `events_in_window` is the
primary access pattern.

### Comparator pattern

Each component subclasses `MethodComparator`:

```python
class TrendSignalComparator(MethodComparator[Series, Series]):
    component = "trend_signal"

    def _compute_metrics(self, output_a, output_b, *, data):
        return {
            "sharpe_a": annualized_sharpe(output_a, data.returns),
            "sharpe_b": annualized_sharpe(output_b, data.returns),
            "turnover_a": turnover(output_a),
            "turnover_b": turnover(output_b),
            "stability_a": 1.0 - param_drift(output_a),
            "stability_b": 1.0 - param_drift(output_b),
        }
```

The `_a` / `_b` suffix convention matters — `evaluate_promotion` reads
those keys directly.

### Dagster wiring

Each signal component gets one asset:

```python
@asset(
    group_name="signals_trend",
    deps=[ingest_yfinance_bars, ingest_fred_series],
)
def signal_trend(context):
    ...
```

Add a `signals_<component>_job` + schedule (daily 00:30 UTC, after
data-quality finishes) and a top-level `compute_all_signals_job`.

## API additions for Stage 3

- `GET /api/v1/signals` — list registered signals + status.
- `GET /api/v1/signals/{id}/values?instrument=…&from=…&to=…&as_of=…`
- `GET /api/v1/signals/values?instrument=…&as_of=…` — cross-component
  latest signal values per instrument.

## Dashboard additions for Stage 3

- `/signals` page with a table per component (trend / carry / value)
  showing latest values × 13 instruments + a sparkline per cell.
- Home: add a "Signals" card with the current cross-component
  composite for the top-5 instruments.

## Open questions to resolve early in Stage 3

1. **Trend signal baseline parameters.** SMA crossover periods? (proposed: 20/60 SMA, configurable).
2. **Carry surrogate.** ETF proxies don't have a futures curve; do we
   skip carry until Stage 12 paid data, or use the FRED `DCOILWTICO`
   vs `DCOILBRENTEU` spread as a proxy?
3. **Promotion oracle.** Stage 2's data quality has no oracle yet; signal
   components need one too. The natural oracle for signals is Sharpe of
   the resulting position series — straightforward but requires a
   walk-forward backtester. Could come from a simplified Stage 9
   walk-forward routine.

## What's stable that Stage 3 can rely on

- Methods framework (Stage 1).
- Ingestion + lineage + freshness (Stage 2).
- Point-in-time query patterns for bars and series.
- Calendar API (`events_in_window`, `next_event`, `is_blackout`).
- `register_all_methods` startup hook in Dagster.
- Daily quality pipeline as a worked example of the "framework + asset
  + comparator + runner" pattern.

## What's NOT stable yet (may change in Stage 3)

- `_load_close_series` is private in `data/quality/runner.py`. Stage 3
  should graduate it to a proper module (`data/loaders.py` or similar)
  with options for return calc, holiday-masking via
  `pandas_market_calendars`, and forward-fill semantics.
- The signal output table doesn't exist yet — Stage 3 creates it.
- Signal-side configuration in YAML (`signals.trend.sma_short`, etc.)
  needs a new `SignalsSettings` block in `config.py`.

## Stage 3 commit checkpoint cadence (suggested)

- After `signals/` module skeleton + trend baseline+shadow + register
- After carry baseline + register
- After value baseline + register
- After signals API routes
- After signals dashboard page
- After tests pass
- After notes complete
