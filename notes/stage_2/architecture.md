# Stage 2 — architecture

A practical "where do I find X?" map for the data + calendar + quality
layers shipped in Stage 2. The Stage 1 framework primitives this layer
relies on are documented in
[`../stage_1/architecture.md`](../stage_1/architecture.md).

## File-by-file map

### DB models — `src/macro_trader/db/models/`

| File | Models |
| --- | --- |
| `market_data.py` | `Instrument`, `DailyBar` (hypertable on `value_ts`) |
| `macro_data.py` | `Series`, `SeriesObservation` (hypertable on `observation_ts`, vintaged), `CalendarEvent` |
| `positioning.py` | `COTWeekly` (hypertable on `report_ts`) |
| `alt_data.py` | `EIAInventory`, `USDAReport`, `WeatherData`, `GoogleTrends` (all hypertables on `value_ts`) |
| `system.py` (extended) | `DataSource`, `DataLineage`, `DataFreshness`, `DataQualityFlag` |

Models are imported from `db/models/__init__.py` so Alembic autogenerate
sees them. Migration `0002_stage2_data_layer.py` creates everything and
calls `create_hypertable()` per table (best-effort if Timescale isn't
installed).

### Data layer — `src/macro_trader/data/`

| File | Role |
| --- | --- |
| `lineage.py` | `IngestStats`, `LineageRecord`, `create_lineage`, `finalize_lineage`, `record_lineage_failure` |
| `freshness.py` | `upsert_freshness_row`, `touch_freshness`, `mark_stale_if_overdue` |
| `instruments.py` | `upsert_instrument`, `list_instruments` |
| `seed/instruments_seed.py` | 13-row seed for the commodity universe |
| `seed/sources_seed.py` | 7-row seed for the data_sources registry + freshness placeholders |
| `ingestion/base.py` | `Ingester[RawData]` ABC. Subclasses implement `fetch / transform / persist`. Base wraps lineage, freshness, heartbeat, log context. |
| `ingestion/{fred,yfinance_source,cftc,eia,usda,noaa,google_trends}.py` | One ingester per source. All idempotent on natural keys (`ON CONFLICT` UPSERT, except USDA which uses delete-then-insert). |
| `ingestion/fred_series.py` | Curated FRED series list with affected-instrument mapping. |
| `quality/methods.py` | `ZScoreOutlier` (BASELINE) + `IsolationForestOutlier` (SHADOW). Both implement the `Method` ABC and serialize via pickle. |
| `quality/comparator.py` | `DataQualityComparator` — flag_rate_{a,b}, IoU, correlation, n_observations. |
| `quality/register.py` | `register(session)` — called by `methods/setup.py`. |
| `quality/runner.py` | `run_daily_quality_check(session)` — loads latest-vintage close per instrument, runs every registered method, persists `DataQualityFlag` rows + a comparison. Idempotent per day. |

### Calendar — `src/macro_trader/calendar/`

| File | Role |
| --- | --- |
| `events.py` | `upsert_event` (natural-key dedup) |
| `linkage.py` | Subject-substring → `affected_instruments` mapping. Stage 4 will refine empirically. |
| `api.py` | `events_in_window`, `next_event`, `is_blackout` (uses Postgres `&&` overlap operator). |
| `sensitivity.py` | Stage 4 placeholder. |
| `ingestion/fred_releases.py` | Pulls FRED `/release/dates` for tracked release IDs (CPI, NFP, GDP, …). |
| `ingestion/eia_schedule.py` | Hardcoded Wed/Thu cadence over next 90 days. |
| `ingestion/usda_schedule.py` | Hardcoded monthly WASDE + weekly Drought Monitor. |
| `ingestion/fomc_schedule.py` | Hardcoded meeting dates with refresh notes. |
| `ingestion/manual.py` | Small seed of OPEC etc. + admin-API entry point in `api/routers/calendar.py`. |

### Methods setup — `src/macro_trader/methods/setup.py`

`register_all_methods(session)` is the central registration entrypoint.
It imports each component's `register.py` and calls them in turn. Dagster
calls it at code-location startup.

### API — `api/routers/`

| Router | Routes |
| --- | --- |
| `data.py` | `GET /data/sources, /data/freshness, /data/lineage`, `/data/series` listing, `/data/series/{id}/observations?as_of=…` (point-in-time), `/data/series/{id}/latest?as_of=…`, `/data/instruments`, `/data/instruments/{id}/bars`, `/data/quality/flags`, `/data/quality/summary` |
| `calendar.py` | `GET /calendar/events`, `/calendar/blackout`, `/calendar/next`, `POST /calendar/events` (admin) |
| `health.py` (extended) | adds `data_freshness` and `data_quality` summaries to the existing health checks |

All wired in `api/main.py:create_app()`.

### Dagster — `orchestration/`

| File | Role |
| --- | --- |
| `assets/ingest.py` | One `@asset` per source. Wraps the ingester `.run()`. Uses `AutoMaterializePolicy.eager`. |
| `assets/calendar.py` | `refresh_calendar_events` — runs all 6 schedule seeders. |
| `assets/data_quality.py` | `daily_data_quality` — depends on `ingest_yfinance_bars`, calls `run_daily_quality_check`. |
| `definitions.py` | Jobs + 7 schedules (UTC) + resources. Bootstraps `register_all_methods` on code-location startup. |

Schedules (UTC):
- `*/5 * * * *` heartbeat
- `0 13 * * *` macro (FRED)
- `0 15 * * *` alt-data (EIA, USDA, NOAA, Google Trends)
- `0 22 * * *` market data (yfinance)
- `0 23 * * *` data quality
- `0 19 * * 5` positioning (CFTC, Friday post-release)
- `0 6 * * 0` calendar refresh (Sunday)

### Frontend — `frontend/src/`

| Page | Role |
| --- | --- |
| `pages/Home.tsx` | Now shows three new cards (Data freshness, Data quality, Calendar) alongside health + methods. |
| `pages/Data.tsx` | 4-tab dashboard: Sources / Series / Instruments / Quality. |
| `pages/Calendar.tsx` | Day-grouped event list with importance filter. |
| `api/client.ts` | Extended with `DataSource`, `Freshness`, `Lineage`, `SeriesRow`, `InstrumentRow`, `BarRow`, `QualityFlag`, `CalendarEvent` types and matching fetchers. |
| `App.tsx` | New protected routes `/data` and `/calendar`. |

### Tests — `tests/`

| File | Coverage |
| --- | --- |
| `unit/data/test_quality_methods.py` | ZScore + IsolationForest unit + hypothesis property tests |
| `unit/data/test_quality_comparator.py` | comparator metrics |
| `unit/data/test_ingester_base.py` | base class invariants |
| `unit/calendar/test_linkage.py` | subject-mapping rules |
| `integration/data/test_lineage_freshness.py` | persistence + failure path |
| `integration/data/test_point_in_time.py` | FRED vintage discipline (as_of query returns correct vintage) |
| `integration/data/test_quality_pipeline.py` | full daily pipeline end-to-end |
| `integration/calendar/test_calendar_api.py` | events_in_window / next_event / is_blackout |

## Key interfaces for Stage 3+

### Reading observations point-in-time (signals must use this)

```python
from sqlalchemy import select, or_
from macro_trader.db.models.macro_data import SeriesObservation

def as_of_value(session, series_id: str, as_of: datetime) -> float | None:
    return session.execute(
        select(SeriesObservation.value)
        .where(SeriesObservation.series_id == series_id)
        .where(or_(
            SeriesObservation.realtime_start.is_(None),
            SeriesObservation.realtime_start <= as_of,
        ))
        .where(or_(
            SeriesObservation.realtime_end.is_(None),
            SeriesObservation.realtime_end >= as_of,
        ))
        .order_by(SeriesObservation.value_ts.desc())
        .limit(1)
    ).scalar_one_or_none()
```

Same pattern for `DailyBar.observation_ts` if/when you need a snapshot
from a specific date.

### Reading the latest close series for signals

```python
from macro_trader.data.quality.runner import _load_close_series

with get_session() as s:
    timestamps, closes = _load_close_series(s, "CL", lookback_days=252)
```

(Stage 3 will graduate this from a private helper to a proper module
function.)

### Filtering events by instrument

```python
from macro_trader.calendar.api import events_in_window, is_blackout

events = events_in_window(
    session, start=t0, end=t1,
    instruments=["CL", "BZ"],
    importance=["high"],
)
if is_blackout(session, instrument_id="CL", ts=now):
    # skip / mask
    ...
```

## Mermaid: Stage 2 connectivity

```mermaid
flowchart LR
    subgraph ext[External]
        FRED[FRED + ALFRED]
        YF[yfinance]
        CFTC[CFTC]
        EIA[EIA]
        USDA[USDA NASS]
        NOAA[NOAA CDO]
        GT[Google Trends]
    end

    subgraph ingest[Ingestion]
        BASE[Ingester base<br/>lineage + freshness + heartbeat]
    end

    subgraph dq[Quality]
        QM[ZScoreOutlier<br/>+ IsolationForestOutlier]
        QC[DataQualityComparator]
        QR[run_daily_quality_check]
    end

    subgraph cal[Calendar]
        CE[upsert_event + linkage]
        CA[events_in_window / next_event / is_blackout]
    end

    subgraph db[(Postgres + TimescaleDB)]
        MD[market_data.daily_bars]
        SO[macro_data.series_observations]
        COT[positioning.cot_weekly]
        ALT[alt_data.*]
        EVT[macro_data.calendar_events]
        FL[system.data_quality_flags]
        CMP[system.method_comparisons]
    end

    ext --> BASE --> MD
    BASE --> SO
    BASE --> COT
    BASE --> ALT
    BASE --> EVT
    MD --> QR
    QR --> QM --> FL
    QR --> QC --> CMP
    CE --> EVT --> CA

    classDef ext fill:#fdf6e3
    classDef ingest fill:#e0f2fe
    classDef dq fill:#fef3c7
    classDef cal fill:#dcfce7
    class FRED,YF,CFTC,EIA,USDA,NOAA,GT ext
    class BASE ingest
    class QM,QC,QR dq
    class CE,CA cal
```
