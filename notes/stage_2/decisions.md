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

<!-- Continued as Stage 2 work progresses. -->
