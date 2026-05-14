# Stage 3 — architecture

Practical "where do I find X?" map for the signal layer.

## Code layout

```
src/macro_trader/
├── data/
│   └── loaders.py                 # Stage 3: public point-in-time data loaders
├── signals/
│   ├── __init__.py                # public surface re-exports
│   ├── base.py                    # SignalMethod ABC + SignalInput/SignalOutput
│   ├── decay.py                   # rolling 252-day Sharpe
│   ├── ensemble.py                # equal-weighted (with regime hook)
│   ├── output.py                  # cross-sectional rank + persistence
│   ├── trend/
│   │   ├── methods.py             # 3 SMAs + HPFilterTrend + TrendEnsemble
│   │   ├── comparator.py
│   │   ├── runner.py
│   │   └── register.py
│   ├── carry/
│   │   ├── methods.py             # CarrySpotProxy (placeholder)
│   │   ├── comparator.py
│   │   ├── runner.py
│   │   └── register.py
│   └── value/
│       ├── methods.py             # ZScoreValue, CrossSectionalValue
│       ├── comparator.py
│       ├── runner.py
│       └── register.py
├── methods/
│   └── setup.py                   # extended with signal-family registers
└── db/models/signals.py           # SignalValue ORM model
```

## DB

`signals.signal_values` (hypertable on `value_ts`).

| Column | Type | Notes |
| --- | --- | --- |
| `signal_id` | TEXT | FK → system.methods_registry.method_id |
| `instrument_id` | TEXT | FK → market_data.instruments.instrument_id |
| `value_ts` | TIMESTAMPTZ | the date the signal refers to |
| `observation_ts` | TIMESTAMPTZ | when computed (as-of anchor) |
| `raw_value` | NUMERIC(28,10) | direct signal output |
| `zscore` | NUMERIC(20,10) | self-z over 252 day lookback |
| `rank` | NUMERIC(8,6) | cross-sectional in [0, 1] |
| `confidence` | NUMERIC(8,6) | in [0, 1]; composite weighting input |
| `rolling_sharpe_252` | NUMERIC(12,6) | in-sample, replaced by Stage 9 OOS |
| `metadata` (`signal_metadata` in Python) | JSONB | signal-specific extras |
| `lineage_id` | UUID | nullable |

PK = `(signal_id, instrument_id, value_ts, observation_ts)`; UPSERT on
conflict.

## Stage 3 methods registered

| method_id | component | status |
| --- | --- | --- |
| `trend.sma_short.v1` | trend_signal | BASELINE |
| `trend.sma_medium.v1` | trend_signal | BASELINE |
| `trend.sma_long.v1` | trend_signal | BASELINE |
| `trend.ensemble.v1` | trend_signal | BASELINE (designated production trend) |
| `trend.hp_filter.v1` | trend_signal | SHADOW |
| `carry.spot_proxy.v1` | carry_signal | BASELINE (placeholder) |
| `value.zscore.v1` | value_signal | BASELINE |
| `value.cross_sectional.v1` | value_signal | SHADOW |

## Dagster

| Asset | Group | Depends on |
| --- | --- | --- |
| `signal_trend` | signals_trend | ingest_yfinance_bars, daily_data_quality |
| `signal_carry` | signals_carry | ingest_yfinance_bars, ingest_fred_series, daily_data_quality |
| `signal_value` | signals_value | ingest_yfinance_bars, daily_data_quality |

Job: `compute_all_signals_job`. Schedule: daily 23:30 UTC.

## API surface

| Method | Path |
| --- | --- |
| GET | `/api/v1/signals` |
| GET | `/api/v1/signals/{id}` |
| GET | `/api/v1/signals/{id}/values?instrument=…&from=…&to=…&as_of=…` |
| GET | `/api/v1/signals/values?instrument=…&as_of=…` |
| GET | `/api/v1/signals/heatmap?as_of=…` |
| GET | `/api/v1/signals/comparisons?component=…` |
| GET | `/api/v1/signals/{id}/decay?instrument=…&lookback_days=…` |

`DESIGNATED_PER_COMPONENT` in `api/routers/signals.py` defines which
method's values populate the heatmap per family. Stage 4+ extends this.

## Frontend

- `/signals` page with three tabs (heatmap / detail / decay).
- Home: "Top signals" card with the cross-component composite
  (`Σ z × confidence` per instrument).
- Methods page automatically shows the eight new signal methods.

## Key interfaces for Stage 4+

### Building a new signal method

1. Subclass `SignalMethod`.
2. Set `metadata = MethodMetadata(method_id="<component>.<algo>.v<n>", ...)`.
3. Implement `compute(data: SignalInput, session) -> list[SignalOutput]`.
4. Write a `<component>/comparator.py` subclassing `SignalFamilyComparator`.
5. Write a `<component>/runner.py` that loads instruments, builds the
   `SignalInput`, calls each method, persists, runs the comparator.
6. Write a `<component>/register.py` and add it to `methods/setup.py`.
7. Add a Dagster asset under `orchestration/assets/signals.py`.

### Point-in-time querying

Always use `macro_trader.data.loaders.load_close_series` (or
`load_close_panel`) with `as_of` set. Direct DB queries should filter
`observation_ts <= as_of` (market) or vintage check (macro).

### Persisting signal outputs

`signals.output.persist_signal_outputs(session, signal_id=..., outputs=
[SignalOutput, ...], lineage_id=...)`. Handles UPSERT + the `metadata`
column quirk. Returns count of rows written.

## Mermaid: Stage 3 connectivity

```mermaid
flowchart LR
    subgraph DATA[Stage 2]
        bars[market_data.daily_bars]
        series[macro_data.series_observations]
    end

    subgraph LOADERS[data/loaders.py]
        L1[load_close_panel]
        L2[load_macro_series]
        L3[compute_returns / compute_vol]
    end

    subgraph SIGNALS[Stage 3 signals]
        trend[Trend: 3 SMAs + ensemble + HP filter]
        carry[Carry: spot proxy placeholder]
        value[Value: z-score + cross-sectional]
    end

    subgraph RESULTS[(signals.signal_values)]
        sv[hypertable on value_ts]
    end

    subgraph COMP[Comparators]
        tc[TrendSignalComparator]
        cc[CarrySignalComparator]
        vc[ValueSignalComparator]
    end

    bars --> L1
    series --> L2
    L1 --> trend
    L1 --> value
    L1 --> carry
    trend --> sv
    carry --> sv
    value --> sv
    trend -.-> tc
    value -.-> vc
    tc --> CMP[(system.method_comparisons)]
    vc --> CMP
```
