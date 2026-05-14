# Runbook (skeleton)

> Stage 1 skeleton. Each later stage fills in the procedures relevant to
> what it adds (e.g. Stage 2 will document data-quality alert response).

## Local setup

```powershell
# Prereqs (one-time): uv, Node 20+, pnpm, Docker Desktop.
python make.py setup            # installs deps, brings up Postgres, runs migrations, seeds admin
python make.py dev-all          # FastAPI :8000, Dagster :3000, frontend :5173
```

On a fresh machine:

```powershell
winget install astral-sh.uv
winget install OpenJS.NodeJS.LTS
npm install -g pnpm
# Docker Desktop installed manually from https://docker.com
```

## Health checks

- `curl http://localhost:8000/api/v1/health` — reports DB, TimescaleDB,
  heartbeat, and methods-framework status.
- `docker compose ps` — services healthy?
- `docker logs macro_trader_postgres` — DB issues.

## Common operations

| Action | Command |
| --- | --- |
| Start services | `python make.py db-up` |
| Stop services | `python make.py db-down` |
| Wipe and rebuild DB | `python make.py db-reset --yes` |
| Run tests | `python make.py test` |
| Lint everything | `python make.py lint` |
| Format Python | `python make.py format` |

## Stage 2: data layer

### Trigger a manual ingest

Bring Dagster up via `python make.py dev-dagster` (or `dev-all`), open
<http://localhost:3000>, navigate to the `ingest_all_job` job, click
**Launch run**. Alternatively from CLI:

```powershell
$env:DAGSTER_HOME = "$PWD\dagster_home"
uv run dagster job execute -j ingest_all_job -f orchestration/definitions.py
```

Per-source jobs are also available: `ingest_market_data_job`,
`ingest_macro_data_job`, `ingest_positioning_job`, `ingest_alt_data_job`,
`ingest_calendar_job`, `data_quality_job`.

### Backfill historical data

Most ingesters take an optional `since` argument in code. The simplest
backfill: temporarily drop the DB, re-run `python make.py setup`, then
launch `ingest_all_job` — every ingester pulls a sensible default window
(yfinance 30d, NOAA 90d, Google Trends 90d, etc.). For deeper history,
construct the ingester directly:

```python
from datetime import datetime, timezone
from macro_trader.config import get_settings
from macro_trader.data.ingestion.fred import FREDIngester
from macro_trader.db.engine import get_sessionmaker

ingester = FREDIngester(session_factory=get_sessionmaker(), settings=get_settings())
ingester.run(since=datetime(2010, 1, 1, tzinfo=timezone.utc))
```

### Investigate a data-quality flag

```bash
# All flags for one series in the last 7 days:
curl "http://localhost:8000/api/v1/data/quality/flags?series_id=market_data.daily_bars:CL:close" | jq

# Per-method summary, last 24h:
curl "http://localhost:8000/api/v1/data/quality/summary?lookback_hours=24" | jq
```

The Dashboard `/data` page → Quality tab shows the same grouped by method.

### Refresh FOMC meeting dates (quarterly)

1. Visit <https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm>.
2. Edit `src/macro_trader/calendar/ingestion/fomc_schedule.py`,
   updating `FOMC_DATES` for the next 18 months.
3. Commit the change. Next run of `ingest_calendar_job` re-seeds the
   calendar (idempotent on natural key).

### Promote `data_quality.isoforest.v1` from SHADOW → PRODUCTION

The first eligible promotion path in the project.

1. Run for at least 180 days. The daily quality job emits one
   `method_comparisons` row per (baseline, shadow, instrument) per day.
2. Eligibility check:
   ```python
   from macro_trader.methods.promotion import PromotionCriteria, evaluate_promotion
   from macro_trader.db.engine import get_session
   crit = PromotionCriteria(
       component="data_quality",
       min_shadow_period_days=180,
       min_comparison_runs=120,  # ~10 instruments * 12 monthly comparisons
       required_improvements=["agreement_iou"],  # adjust to your gate
       improvement_threshold=0.05,
   )
   with get_session() as session:
       eligible, evidence = evaluate_promotion(
           "data_quality.isoforest.v1", crit, session=session,
       )
   print(eligible, evidence)
   ```
3. If eligible, an admin POSTs:
   ```bash
   curl -X POST http://localhost:8000/api/v1/methods/data_quality.isoforest.v1/status \
        -H "Authorization: Bearer $ADMIN_JWT" \
        -H "Content-Type: application/json" \
        -d '{"status": "production", "reason": "180-day shadow met agreement_iou threshold"}'
   ```
   The registry automatically demotes `data_quality.zscore.v1` (the
   BASELINE) to DEPRECATED.

## Auth

- Admin credentials come from `.env` (`ADMIN_EMAIL`, `ADMIN_PASSWORD`).
- `python make.py setup` runs `scripts/seed_test_data.py` which creates the
  admin row if missing.
- Reset admin password: edit `.env`, then re-run `python make.py db-reset
  --yes` (destructive) OR open `psql` and `UPDATE auth.users SET
  hashed_password = '...'` with a freshly hashed value.

## Methods framework operations

- List methods: `curl http://localhost:8000/api/v1/methods | jq`.
- Inspect history for a method:
  `curl http://localhost:8000/api/v1/methods/<id>/history | jq`.
- Promote a shadow to production (admin):
  ```bash
  curl -X POST http://localhost:8000/api/v1/methods/<id>/status \
       -H "Authorization: Bearer <admin jwt>" \
       -H "Content-Type: application/json" \
       -d '{"status": "production", "reason": "promotion approved 2026-Q3"}'
  ```
  The registry automatically demotes the existing production method to
  `deprecated`.

## Troubleshooting

- **uv sync fails on first run with "file not found: README.md"** — README
  must exist before `uv sync`. Stage 1 ships one; if you wipe the repo,
  recreate a placeholder before re-running.
- **Alembic "schema system does not exist"** — fixed by `alembic/env.py`
  which creates the schema before applying the version table. If you hit
  this on a custom DB, run `CREATE SCHEMA system` manually.
- **Postgres ENUM "invalid input value 'BASELINE'"** — SQLAlchemy `Enum`
  must use `values_callable=lambda x: [e.value for e in x]` so it sends
  lowercase values, matching the DB enum.
- **pnpm test exits non-zero** — pnpm wraps test runs in a re-install that
  surfaces the `ERR_PNPM_IGNORED_BUILDS` warning as a non-zero exit. `make.py
  test-frontend` invokes `vitest` directly to bypass this.

## When things go really wrong

- Drop everything and start over (DESTRUCTIVE):
  ```powershell
  docker compose down -v        # wipes the data volume
  Remove-Item -Recurse -Force .venv, frontend\node_modules
  python make.py setup
  ```
