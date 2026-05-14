# Stage 1 → Stage 2 handoff

What Stage 2 (Data Layer) needs to know to start.

## Stage 2 scope (reminder)

Free-source ingestion, point-in-time discipline, calendar events, data
quality methods. Universe: ETF proxies of futures (USO, BNO, UNG, UHN,
UGA, CPER, JJU, GLD, SLV, PPLT, CORN, SOYB, WEAT) + macro series from FRED.

Critical: Stage 2 must register the **first** real methods through the
framework. Specifically, data-quality outlier detection:

- `data_quality.zscore.v1` as `BASELINE` (rolling |z| > 3 over a 60-day
  window per series).
- `data_quality.isoforest.v1` as `SHADOW` (sklearn IsolationForest with
  per-series contamination).
- `DataQualityComparator(MethodComparator)` running daily on the prior
  day's universe.

## Data-layer conventions Stage 2 should establish

1. **Point-in-time discipline**: every observation gets an
   `observation_ts` (when the value was published/known) in addition to
   the value-time. Backtests must never see future data.
2. **Schema-per-source-and-type**:
   - `market_data.daily_bars` — OHLCV bars, hypertable on
     `value_ts`.
   - `market_data.intraday_bars` — when/if we add intraday in a later
     stage; not Stage 2.
   - `macro_data.series_observations` — FRED-style long form
     (series_id, value_ts, observation_ts, value, vintage).
   - `positioning.cot_weekly` — CFTC COT reports.
   - `alt_data.*` — placeholder until Stage 5.
3. **TimescaleDB hypertables**: enable on every time-series table at
   creation time:
   ```sql
   SELECT create_hypertable('market_data.daily_bars', 'value_ts',
                            if_not_exists => TRUE);
   ```
4. **One ingest asset per source per granularity** in `orchestration/assets/`,
   group by source. Each asset:
   - is idempotent (UPSERT on natural key);
   - emits its own row to `system.heartbeat` with `source = "ingest.<name>"`;
   - logs to structlog with `bind_contextvars(asset_key=..., partition=...)`.
5. **Symbol mastering**: `market_data.instruments` table mapping our
   internal symbol (e.g. `CL`) to ETF proxy ticker (`USO`), provider
   metadata, currency, exchange. Use this for joins; don't hard-code
   tickers in signal code.

## Calendar events (Stage 2)

- `macro_data.calendar_events` — release dates, FOMC, EIA inventory, USDA
  WASDE. One row per event with `event_ts` (planned/actual), `kind`,
  `subject` (which instruments / series it affects), and a JSON metadata
  blob.
- The Calendar package (`src/macro_trader/calendar/`) exposes
  `is_blackout(symbol, ts)` and `next_event(symbol, after=ts)`.
- Stage 4's catalyst-sensitivity signals depend on this — get the schema
  right now.

## Data quality methods Stage 2 must register

### Baseline: `data_quality.zscore.v1`

```python
class ZScoreOutlier(Method[Series, BoolMask]):
    metadata = MethodMetadata(
        method_id="data_quality.zscore.v1",
        component="data_quality",
        name="Rolling z-score outlier flag",
        version="1.0.0",
        description="Flag |z| > 3 over a 60 trading-day rolling window per series.",
        references=[],
    )
    # fit is a no-op; predict returns a bool mask.
```

### Shadow: `data_quality.isoforest.v1`

```python
class IsolationForestOutlier(Method[Series, BoolMask]):
    metadata = MethodMetadata(
        method_id="data_quality.isoforest.v1",
        component="data_quality",
        name="Isolation Forest outlier",
        version="1.0.0",
        description="sklearn IsolationForest, contamination=0.01, per-series.",
        references=["Liu, Ting, Zhou 2008"],
    )
    # fit stores trained estimator; serialize via pickle.
```

### Comparator

```python
class DataQualityComparator(MethodComparator[Series, BoolMask]):
    def __init__(self):
        super().__init__(component="data_quality")

    def _compute_metrics(self, output_a, output_b, *, data):
        return {
            "flag_rate_a":   float(output_a.mean()),
            "flag_rate_b":   float(output_b.mean()),
            "agreement_iou": iou(output_a, output_b),
            # When we have hand-labelled outliers (rare), also:
            #   recall_a, recall_b, precision_a, precision_b
        }
```

Schedule the comparator daily, against the prior trading day's full
universe.

## Things that should change in Stage 2 but didn't get done in Stage 1

- Add `pre-commit install` to `make.py setup`. We deferred it; with the
  data layer there'll be more file churn and stricter formatting helps.
- Add `alembic check` to backend CI to catch model/migration drift.
- Move from a single `alembic_version` in `system` to per-domain version
  tracking *only if* we end up wanting independent schema deployment.
  Probably still not needed.
- Add a Dagster job that cleans up revoked / expired refresh tokens older
  than 30 days. Trivial; just put it on a daily schedule.

## API additions Stage 2 may need

- `GET /api/v1/data/series/{series_id}/latest` (with `as_of` query param
  for point-in-time).
- `GET /api/v1/data/quality?as_of=...&symbol=...` returning the latest
  data-quality flags from whichever method is `PRODUCTION` in the
  `data_quality` component.

## Dashboard additions Stage 2 may need

- A small "Data Quality" card on the Home page showing today's flag count
  + linked to a Stage 11 Universe Scan view.

## Open questions to resolve early in Stage 2

1. **Vintaged data**: do we store every release vintage (e.g. FRED's
   `observation_date` + `realtime_start`) or only the latest known value
   tagged with `observation_ts`? Recommendation: store every vintage —
   it's mandatory for honest backtesting in Stage 9 and trivially cheap
   in TimescaleDB.
2. **Holiday calendars**: which provider for trading holidays per
   exchange? `pandas_market_calendars` is the obvious pick; verify
   licence (BSD).
3. **ETF→futures mapping**: documented in `notes/stage_2/decisions.md`
   when you make the call. Recommendation: linear roll-adjustment via
   total-return ETF when the underlying futures contract is unavailable
   in the free tier.

## What's stable that Stage 2 can rely on

- Methods framework API (`register_method`, `MethodStatus`,
  `MethodComparator`, `evaluate_promotion`).
- Config layering and env-var override behaviour.
- Logging context-binding pattern.
- Alembic environment setup (`system` schema pre-created, extensions
  enabled).
- Dagster `Definitions` pattern, including how resources are wired.
- Auth & dependency aliases (`SessionDep`, `SettingsDep`, `AdminDep`).

## What's NOT stable yet (may change in Stage 2)

- The way Methods get **registered** at app startup. Today there's no
  global "register all methods" entry point — each method is registered
  by whatever code touches it first. Stage 2 should introduce
  `orchestration/setup_methods.py` (idempotent registration on Dagster
  startup) to make the registry self-rebuilding from code.
- Method serialization to the DB. Stage 1 has the column ready but no
  flow uses it yet. Stage 2's IsoForest is the first method with non-trivial
  fitted state.
