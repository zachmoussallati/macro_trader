# Stage 2 — decisions

A running log of choices made while building the data layer. Each decision:
**what / why / alternatives considered / consequence**.

## 1. Move real API keys to `.env`; restore `.env.example` to placeholders

- **What**: User pasted real FRED / EIA / USDA / NOAA / Alpha Vantage keys
  into the tracked `.env.example`. Moved them to `.env` (gitignored), restored
  placeholders in `.env.example` with sign-up URLs in the comments.
- **Why**: `clean_branch` is now the public default branch. Committing real
  API keys would leak them. `.env.example` exists to document the schema for
  new contributors, not to hold secrets.
- **Consequence**: secrets live exclusively in `.env`. If `.env` were ever
  pushed by accident, `.gitignore` line 41 would refuse it.

## 2. Pre-commit hooks installed

- **What**: `.pre-commit-config.yaml` with ruff (check + format),
  trailing-whitespace, end-of-file-fixer, check-yaml, check-toml,
  check-added-large-files (512KB), check-merge-conflict, detect-private-key.
  `python make.py setup` now runs `pre-commit install` so hooks fire on every
  `git commit`.
- **Why**: Stage 1 deferred this until the codebase stabilised. Stage 2
  adds ~20 new files per commit checkpoint; hooks catch the easy stuff
  before it gets reviewed.
- **Consequence**: bypass via `--no-verify` if a hook is wrong; otherwise
  the commit fails fast.

## 3. Node pinned to 20.18.0 in `.nvmrc`; engines kept permissive

- **What**: `.nvmrc` = `20.18.0`. `frontend/package.json` engines.node =
  `">=20.0.0 <21.0.0"`. **But** added `frontend/.npmrc` with
  `engine-strict=false`.
- **Why**: spec wants Node 20 (broad ecosystem compat). The developer
  machine already has Node 24 installed (winget's default LTS). Forcing a
  downgrade requires manual winget uninstall / nvm install. `engine-strict
  =false` means pnpm warns rather than rejects on Node 24, so the project
  documents 20 as canonical without breaking the existing setup.
- **Alternatives considered**: (a) make engines fully open `">=20"` — loses
  the documented version; (b) hard-fail on Node 24 — breaks dev loop.
- **Consequence**: new contributors with nvm get 20.18.0 automatically;
  existing setup keeps working.

## 4. Method registration entrypoint: code-location startup (not first-run sensor)

- **What**: `src/macro_trader/methods/setup.py` exposes
  `register_all_methods(session)`. `orchestration/definitions.py` calls it
  at module import time (Dagster code-location load), wrapped in a
  try/except that logs but does not raise.
- **Why**: simpler than a Dagster sensor. The registry must be present
  before any asset materialises; a sensor only fires after the location
  loads, so the first asset run could see an empty registry. Code-location
  startup guarantees registration happens before the first asset run.
  Failures are logged and the next boot retries — a transient DB blip
  shouldn't make every asset undeployable.
- **Alternatives considered**: (a) first-run sensor — adds latency and
  failure surface; (b) every asset registers its own method — duplicative,
  defeats the central registry pattern.
- **Consequence**: Dagster restarts re-register; idempotency in the
  registry prevents duplicate rows.

## 5. `alembic check` added to backend CI; mypy promoted to strict

- **What**: CI now runs `alembic upgrade head` + `alembic check`. Catches
  "added a model column, forgot to autogenerate a migration".
- **What**: removed `continue-on-error: true` from the mypy step. Stage 1
  cleaned mypy errors and added an `orchestration.*` override; strict mypy
  is now achievable across the whole codebase.
- **Why**: stages 2+ add a lot of schema. Drift between models and
  migrations is the single most common cause of silent data corruption on
  this kind of system.
- **Consequence**: any new model needs a migration before CI passes.

## 6. Test config skips `.env` loading

- **What**: `get_settings()` only calls `load_dotenv()` when `APP_ENV !=
  test`. Conftest sets `APP_ENV=test` before any imports.
- **Why**: with real keys in `.env`, `load_dotenv()` was bleeding
  `APP_LOG_LEVEL=INFO` and `DATABASE_URL=postgresql://...` into tests that
  expected dev.yaml/test.yaml defaults. The previous "POSTGRES_DB force
  override" only protected one field; the right fix is to isolate test env
  from developer env entirely.
- **Consequence**: tests are hermetic again. Conftest must set every env
  var the test cares about. Future tests that depend on a value should
  monkeypatch it explicitly.

## 7. Every FRED vintage stored, not just latest

- **What**: `FRED.get_series_all_releases` returns every revision; we
  store each as its own row keyed by `(series_id, value_ts, observation_ts)`.
  `realtime_start` / `realtime_end` mirror ALFRED.
- **Why**: Stage 9 backtests must never see future data. A revision
  published 2 months after the initial release is **not** what a trading
  system would have seen on the original date.
- **Consequence**: ~2-5x storage cost vs latest-only. Acceptable. Query
  by `as_of` returns exactly the vintage in effect at that time.

## 8. Holiday calendar: `pandas_market_calendars` (added but unused in S2)

- **What**: deps include `pandas-market-calendars`. No code uses it yet.
- **Why**: Stage 3+ will mask trading days for return computations.
  Adding the dep now keeps Stage 2 self-contained.
- **Consequence**: small dep bloat; will be exercised in Stage 3.

## 9. Per-ingester rate-limit handling: `tenacity` retry decorator

- **What**: each ingester wraps its raw HTTP call with `@retry(stop=
  stop_after_attempt(3), wait=wait_exponential(multiplier=2, max=30))`.
- **Why**: per-source failures shouldn't abort the run; retry is the
  cheapest first defence. Per-row try/except in `fetch` keeps one bad
  series from killing the rest of the batch.
- **Alternatives considered**: a global token-bucket — overkill given we
  call each API a handful of times per day.
- **Consequence**: total backoff capped at ~93 seconds per source.

## 10. ETF proxy ↔ instrument: one row per commodity in `instruments`

- **What**: 13 rows in `market_data.instruments` keyed by our internal
  symbol. `proxy_ticker` stores the ETF, `underlying_ref` stores the
  eventual paid-data symbol.
- **Why**: signals join on `instrument_id`. Hard-coding `"USO"` in
  signal code would require N rewrites when we swap to NYMEX futures
  in Stage 12.
- **Consequence**: paid-data migration is a one-line update of
  `proxy_ticker`.

## 11. `usda_reports` PK is composite `(report_id, value_ts)`

- **What**: TimescaleDB requires the partitioning column to appear in
  the PK. Adding `value_ts` to `usda_reports`' PK alongside `report_id`
  satisfies that without losing UUID-keyed identity.
- **Why**: every other Stage 2 hypertable already has a natural composite
  PK; USDA was the only one with a synthetic UUID PK.
- **Consequence**: `ON CONFLICT` is unusable on this table; the ingester
  uses delete-then-insert per `(report_type, value_ts, commodity, metric)`.

## 12. ARRAY columns: use `sqlalchemy.dialects.postgresql.ARRAY`

- **What**: switched `affected_instruments` and `affected_series` columns
  from `sqlalchemy.ARRAY` to `sqlalchemy.dialects.postgresql.ARRAY`.
- **Why**: `.overlap()` (Postgres `&&` operator) is only available on the
  postgres-typed column. Calendar API needs it for instrument filtering.
- **Consequence**: model code is Postgres-specific — fine, we're not
  pretending to be portable.

## 13. mypy: `macro_trader.data.ingestion.*` relaxed

- **What**: per-module override drops strict typing for ingesters.
- **Why**: external libs without stubs (`yfinance`, `fredapi`, `pytrends`)
  would otherwise drown the checker. Strict on the rest of the codebase,
  including `macro_trader.calendar.*` and `data.quality.*`.
- **Consequence**: subtle bugs in ingester code aren't caught by mypy.
  Mitigated by integration tests + lineage records that capture failures.

## 14. Daily quality runner is idempotent on `(method, series, day)`

- **What**: `run_daily_quality_check` deletes the day's flag rows for
  each `(method_id, series_id)` pair before inserting fresh ones.
- **Why**: reruns must not double-count. `ON CONFLICT` on a UUID PK is
  unhelpful; delete-then-insert is clearer.
- **Consequence**: a partial failure mid-run could leave some methods
  with cleared rows and no replacements. Acceptable for Stage 2 — Stage 9
  backtests don't rely on partial daily state.

## 15. TimescaleDB auto-indexes filtered in alembic env.py

- **What**: `_TIMESCALE_AUTO_INDEXES` lists `<table>_<time_col>_idx`
  for each hypertable. `include_object` returns False for those during
  autogenerate so `alembic check` doesn't constantly want to drop them.
- **Why**: TimescaleDB creates a descending btree index on the partition
  column automatically. Our model metadata doesn't (and shouldn't) know
  about it.
- **Consequence**: adding a new hypertable requires extending the set.
  Documented in the env.py header.

