# Stage 2 — tradeoffs and deferrals

What was deliberately skipped or shortcut, and why.

## Deferred

### Baltic indices, LME stocks, satellite imagery, news feeds
Out of Stage 2 scope (already noted in the prompt). These are scrape-
fragile, paid-only, or both. Stage 5 will revisit alt-data coverage.

### CFTC legacy + TFF reports
Only the disaggregated futures-only report is wired. The schema supports
both via `report_type`, and the COT contract-code map is shared. Adding
the other two reports is a copy of `cftc.py` with different field
mappings. Defer until a signal actually needs the legacy commercial
breakdown.

### Crop Progress + Grain Stocks ingestion
USDA scaffolding only ingests WASDE-style production/yield rows for the
big three grains. Quarterly Grain Stocks and weekly Crop Progress are
covered by the schema (`alt_data.usda_reports.report_type`) but not yet
fetched. Stage 4's agricultural signals will pull those queries.

### NOAA station-level coverage
We ingest the GSOM US-aggregate HDD/CDD only. Station-level coverage
for major agricultural regions is needed for Stage 4 yield-impact
signals; deferred until then.

### Google Trends backfill
We only fetch 90 days. Google Trends rate-limiting makes >1-year
backfills unreliable; we accept the short window and let it grow
forward.

### Per-domain Alembic versions
Still a single `alembic_version` table in `system`. Per-domain version
tracking adds maintenance burden without a real need.

### Fitted-model storage in DB
`MethodRegistryRow.serialized_blob` exists but is unused. The
IsolationForest in Stage 2 refits per day from the raw data (in
`runner.py`), so persisted state isn't required. Stage 6 (regime
classifier) will likely be the first to need cross-run model state.

### Frontend sparklines / charts
The Data > Series tab shows series metadata in a table. A sparkline of
the latest values would be nicer; Stage 11 (dashboard) will add
proper charting using Recharts + Plotly + Lightweight Charts.

### Holiday-calendar masking in ingesters
`pandas-market-calendars` is in deps but unused. Stage 3 will need it
for return calcs; ingesting on weekends/holidays is currently fine
because providers return empty results.

### Refresh-token cleanup job
Same as Stage 1's deferral. Add a `Cleanup` Dagster asset in Stage 3 or
later.

### Promotion of `data_quality.isoforest.v1`
Not eligible until 180 days of comparison runs are in. The framework is
ready; the operator promotion path is documented in `runbook.md`.

### Coverage % target on data_layer
Currently around 44% on `macro_trader.*`. Some ingesters have 0%
coverage (no live API hits in tests; unit tests would need mocked HTTP
fixtures). Stage 2 prioritises end-to-end pipeline correctness over
line coverage; we'll raise the target as signals mature.

## Shortcuts taken

### FRED `_fetch_one` is single-threaded
We sleep 50ms between series even though FRED allows 120/min sustained.
Stage 2 is well under the limit; concurrency would help only for very
large backfills. Add a thread pool if/when Stage 9 backfills become slow.

### CFTC contract-code mapping is hand-curated
`CFTC_CODE_TO_INSTRUMENT` is a static dict. The code that CFTC publishes
for Brent has rotated in the past. Document a quarterly review in the
runbook (TODO: actually add it).

### USDA ingester uses delete-then-insert
Because `usda_reports` PK is `(report_id, value_ts)` and `report_id` is
a UUID generated server-side, there's no natural-key `ON CONFLICT`. The
delete-then-insert per `(report_type, value_ts, commodity, metric)` is
correct but more expensive than UPSERT.

### Calendar is hardcoded for most schedules
EIA / USDA / FOMC use hardcoded cadence + dates, refreshed manually. A
fully automated discovery would scrape the providers' calendar pages —
fragile, low ROI for Stage 2. The refresh procedure is documented in
the runbook and the FOMC dates have an explicit refresh comment in the
source file.

### TimescaleDB hypertable migration is best-effort
The migration calls `create_hypertable(...)` only if the timescaledb
extension is loaded. Test DBs sometimes get a partial install of the
extension; this lets tests still pass without the hypertable transform
applied. Production DBs always have it.

### Daily quality runner: per-instrument loop is sequential
Could be parallelised by instrument since they don't share state.
Stage 2 covers 13 instruments × <1s each so it's a non-issue.

### No retries on the data-quality runner itself
The `daily_data_quality` asset doesn't retry on failure. If half the
instruments process and then the DB connection drops, the partial-run
records are committed and the next day's run continues. Stage 9 may
want stronger guarantees.

### Frontend has no test for /data or /calendar
The smoke tests only cover App + Login + Methods. Stage 3's signal
dashboard will add proper Playwright coverage; vitest for these new
pages is on the list.

## What we'd revisit

- **NaN handling in FRED values**: currently coerced to `None` silently.
  A dedicated audit pass would flag the first occurrence per series.
- **HTTP timeout defaults**: ingesters use 30-60s. EIA + NOAA can be
  slow; tune in production once we see real timings.
- **CFTC year boundary**: ingesting only the current year means the
  Jan 1 ingest finds zero rows. Add a New-Year sentinel that fetches the
  prior year on Jan 1-7.
