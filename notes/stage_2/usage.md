# Stage 2 — usage

## First-time setup additions

Once Stage 1 setup is done, get free API keys for:

| Service | URL | Env var |
| --- | --- | --- |
| FRED | <https://fred.stlouisfed.org/docs/api/api_key.html> | `FRED_API_KEY` |
| EIA | <https://www.eia.gov/opendata/register.php> | `EIA_API_KEY` |
| USDA Quick Stats | <https://quickstats.nass.usda.gov/api> | `USDA_API_KEY` |
| NOAA CDO | <https://www.ncdc.noaa.gov/cdo-web/token> | `NOAA_API_KEY` |

Add them to your local `.env` (never `.env.example`). yfinance, CFTC, and
Google Trends need no auth.

```powershell
notepad .env   # fill in the four keys
```

Re-run `python make.py setup` only if `.env` didn't already exist.
Otherwise just restart the dev services so they pick up the new vars.

## Daily dev loop (unchanged from Stage 1)

```powershell
python make.py dev-all
```

- FastAPI: <http://localhost:8000> (`/docs`, `/redoc`).
- Dagster: <http://localhost:3000>.
- Vite: <http://localhost:5173>.

New routes in the dashboard:

- `/data` — Sources / Series / Instruments / Quality tabs.
- `/calendar` — month-grouped event list.

New API endpoints (full list in `docs/architecture.md`):

- `GET /api/v1/data/sources`
- `GET /api/v1/data/instruments`
- `GET /api/v1/data/series/{id}/observations?as_of=2024-06-30`
- `GET /api/v1/data/quality/flags?lookback_days=7`
- `GET /api/v1/calendar/events?from=…&to=…&importance=high`
- `POST /api/v1/calendar/events` (admin only)

## Trigger a manual ingest

From the Dagster UI: launch `ingest_all_job` (covers every source +
calendar). For one source only, launch e.g. `ingest_market_data_job`
or `ingest_macro_data_job`.

From the CLI:

```powershell
$env:DAGSTER_HOME = "$PWD\dagster_home"
uv run dagster job execute -j ingest_all_job -f orchestration/definitions.py
```

## Backfill historical data

Each ingester accepts a `since` keyword. Default windows:

| Ingester | Default lookback |
| --- | --- |
| FRED | every vintage (no limit; bounded by ALFRED retention) |
| yfinance | 30 days |
| CFTC | current year |
| EIA | every available row (per-series) |
| USDA | every available row (per-query) |
| NOAA | 90 days |
| Google Trends | 90 days |

Deep backfill (e.g., 10 years of FRED):

```python
from datetime import datetime, timezone
from macro_trader.config import get_settings
from macro_trader.data.ingestion.fred import FREDIngester
from macro_trader.db.engine import get_sessionmaker

ingester = FREDIngester(
    session_factory=get_sessionmaker(),
    settings=get_settings(),
)
ingester.run(since=datetime(2014, 1, 1, tzinfo=timezone.utc))
```

## Investigate a data-quality flag

```bash
# All flags from the last 7 days for a specific instrument:
curl "http://localhost:8000/api/v1/data/quality/flags?instrument_id=CL&lookback_days=7" | jq

# Per-method summary for the last 24h:
curl "http://localhost:8000/api/v1/data/quality/summary?lookback_hours=24" | jq

# Filter by method:
curl "http://localhost:8000/api/v1/data/quality/flags?method_id=data_quality.zscore.v1" | jq
```

In the dashboard: `/data` → **Quality** tab.

## Refresh FOMC dates (quarterly maintenance)

1. Visit <https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm>.
2. Edit `src/macro_trader/calendar/ingestion/fomc_schedule.py`, updating
   `FOMC_DATES` to cover the next 18 months.
3. Commit. Next `ingest_calendar_job` run will pick it up (idempotent on
   natural key).

## Add a manual calendar event

Quickest: admin POST.

```bash
ACCESS=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"change_me_dev_admin_password"}' \
  | jq -r .access_token)

curl -X POST http://localhost:8000/api/v1/calendar/events \
  -H "Authorization: Bearer $ACCESS" \
  -H "Content-Type: application/json" \
  -d '{
    "event_ts": "2026-08-15T13:00:00Z",
    "kind": "geopolitical",
    "subject": "OPEC+ technical meeting",
    "importance": "medium",
    "region": "GLOBAL",
    "affected_instruments": ["CL", "BZ"]
  }'
```

## Inspect lineage

```bash
# Most recent 50 lineage records (across all sources):
curl "http://localhost:8000/api/v1/data/lineage" | jq

# Filter to one source:
curl "http://localhost:8000/api/v1/data/lineage?source=fred" | jq

# Or in psql:
docker exec -it macro_trader_postgres psql -U macro -d macro_trader \
  -c "SELECT source_id, fetched_at, rows_ingested, error_count FROM system.data_lineage ORDER BY fetched_at DESC LIMIT 10;"
```

## Promote a data-quality method (after 180 days of shadow)

See `docs/runbook.md` → "Promote `data_quality.isoforest.v1`" section.
Eligibility check:

```python
from macro_trader.methods.promotion import PromotionCriteria, evaluate_promotion
from macro_trader.db.engine import get_session

crit = PromotionCriteria(
    component="data_quality",
    min_shadow_period_days=180,
    min_comparison_runs=120,
    required_improvements=["agreement_iou"],
    improvement_threshold=0.05,
)
with get_session() as session:
    eligible, evidence = evaluate_promotion(
        "data_quality.isoforest.v1", crit, session=session,
    )
print("eligible:", eligible)
print(evidence)
```

## Common ops table

| Action | Command |
| --- | --- |
| Manual ingest (all sources) | Dagster UI → `ingest_all_job` |
| Manual ingest (one source) | Dagster UI → `ingest_<source>_job` |
| Manual data-quality run | Dagster UI → `data_quality_job` |
| Manual calendar refresh | Dagster UI → `ingest_calendar_job` |
| List freshness via API | `curl localhost:8000/api/v1/data/freshness` |
| Check pending alembic ops | `uv run alembic check` |
| Run all tests | `python make.py test` |
| Lint everything | `python make.py lint` |
