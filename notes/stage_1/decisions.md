# Stage 1 — decisions

A running log of choices made while building the foundation. Each decision
has the form: **what / why / alternatives considered / consequence**.

## 1. Package manager: `uv` (not Poetry / pip-tools)

- **What**: use `uv` to manage Python deps and the virtualenv.
- **Why**: uv is dramatically faster (Rust-backed resolver), one tool for
  Python versions + deps + venv + lockfile, and explicitly requested in the
  spec.
- **Alternatives**: Poetry (slower, more legacy quirks), Hatch (fine but no
  team familiarity).
- **Consequence**: `uv.lock` committed; CI installs via `uv sync --extra dev`;
  contributors must install `uv` (one `winget install astral-sh.uv`).

## 2. Build backend: `uv_build` from src layout

- **What**: `[build-system].build-backend = "uv_build"`; package root is
  `src/macro_trader`.
- **Why**: zero-config; `uv_build` recognises the src layout, supports
  editable installs natively. Avoids the hatchling/setuptools matrix entirely.
- **Consequence**: dropping uv would require swapping the backend, but the
  package layout is conventional and easy to migrate.

## 3. Python 3.12 (not 3.13)

- **What**: `requires-python = ">=3.12,<3.13"`.
- **Why**: many scientific deps (statsmodels, dagster integrations) still
  have rough edges on 3.13. 3.12 has every feature we use (PEP 695 generics,
  `StrEnum`, structural pattern matching, `tomllib`).
- **Consequence**: revisit when 3.13 ecosystem support is broad.

## 4. Node 24 LTS (not 20)

- **What**: spec said Node 20 LTS; actually installed Node 24.15.0 via
  `winget install OpenJS.NodeJS.LTS` (winget pulls latest LTS).
- **Why**: winget's default behaviour; switching to 20 would require manual
  installer download. Vite 5 / TypeScript 5.6 work fine on 24.
- **Consequence**: documented here so future audits don't think it's an
  accident. If 24 causes problems we can pin to 20 with `nvm` later.

## 5. SQLAlchemy 2.x typed style with `DeclarativeBase`

- **What**: all models use `Mapped[T]` / `mapped_column(...)` syntax with a
  shared `Base(DeclarativeBase)`.
- **Why**: mypy-friendly, the modern standard. Tooling support (Alembic
  autogenerate, dataclasses) all converge on this.
- **Alternatives**: legacy `Column` / `declarative_base()` — works but is
  poorly-typed.
- **Consequence**: contributors writing models need 2.x familiarity.

## 6. Postgres native enums for `MethodStatus` and `UserRole`

- **What**: native `CREATE TYPE method_status AS ENUM (...)` rather than
  `String` columns + `CHECK` constraints.
- **Why**: enforced at the DB level; clear migration semantics; pgAdmin can
  enumerate values. The DB is the source of truth.
- **Gotcha** (fixed mid-build): SQLAlchemy's `Enum(MyEnum)` defaults to
  sending the **name** (`BASELINE`) rather than the **value** (`baseline`).
  All `SAEnum(...)` declarations now use
  `values_callable=lambda x: [e.value for e in x]` to make this explicit.
- **Consequence**: adding a new status requires a migration (`ALTER TYPE`),
  not just a Python enum update. Accepted.

## 7. Schemas per domain (not per table)

- **What**: `market_data`, `positioning`, `macro_data`, `alt_data`,
  `signals`, `regime`, `portfolio`, `backtest`, `claude_layer`, `auth`,
  `system`.
- **Why**: cross-domain joins are explicit; access can later be granted per
  schema; Alembic migrations stay focused.
- **Consequence**: every model must declare `schema=` in `__table_args__`.
  Alembic `version_table_schema="system"` puts the bookkeeping table
  alongside the other system tables.

## 8. Alembic `version_table_schema = "system"` + env.py pre-create

- **What**: `alembic/env.py` runs `CREATE SCHEMA IF NOT EXISTS "system"`
  before configuring Alembic.
- **Why**: without this, the very first migration fails because Alembic
  tries to create `system.alembic_version` in a schema that doesn't exist
  yet. The first migration creates the schemas via `op.execute`, but that
  runs *after* the version table is created.
- **Alternatives**: put the version table in `public` (rejected — keep
  `public` empty); split into two migrations (rejected — fragile).
- **Consequence**: env.py owns the responsibility for ensuring the system
  schema exists, including in CI ephemeral DBs.

## 9. Methods framework: in-memory registry mirrored to DB (not DB-only)

- **What**: `MethodRegistry` holds method instances in a thread-safe dict;
  `system.methods_registry` is the durable mirror.
- **Why**: a Method instance often holds fitted model state (sklearn
  estimator, fitted covariance matrix). Loading those from a blob on every
  call is wasteful. Process-level cache keeps runtime fast; DB
  reconstructs registry on cold start.
- **Consequence**: status transitions must update both. Code path for
  `set_status` writes to both.

## 10. Promotion is always manual

- **What**: `evaluate_promotion` returns `(eligible, evidence)`. It never
  mutates state. To promote, a human calls `POST
  /api/v1/methods/{id}/status`.
- **Why**: even with a strict eligibility gate, promoting a method changes
  what trades. That decision must be auditable and human-owned. The dashboard
  shows the evidence so the operator has the numbers in front of them.
- **Consequence**: never call `set_status(... PRODUCTION)` from code; always
  from CLI/API after review.

## 11. Default test-config relaxes promotion criteria

- **What**: `config/test.yaml` sets `min_shadow_period_days=0`,
  `min_comparison_runs=1`, `improvement_threshold=0.0`.
- **Why**: lets us write fast integration tests that exercise the gate
  without time-travel hacks.
- **Consequence**: tests document the gate; production / dev defaults remain
  conservative.

## 12. structlog with `contextvars` for request_id propagation

- **What**: `api/main.py` middleware sets `request_id` via
  `structlog.contextvars.bind_contextvars`; all logs in a request carry it.
- **Why**: async-safe context propagation. The same `get_logger()` API works
  inside Dagster jobs (different context) and tests (no context).
- **Consequence**: future stages should `bind_contextvars(job_id=...)` etc.
  rather than passing logger objects around.

## 13. JWT auth + refresh-token rotation

- **What**: short-lived access JWT (60 min) + long-lived refresh token
  (30 days) stored hashed in `auth.refresh_tokens`. Refresh rotates: each
  successful refresh revokes the used token and issues a new pair.
- **Why**: standard, dashboard-friendly. Refresh rotation provides modest
  defence against stolen refresh tokens (subsequent use detected).
- **Consequence**: refresh requires a DB hit and bcrypt verification. Fine
  at our scale.

## 14. Frontend uses TanStack Query + Zustand (not Redux)

- **What**: server state in TanStack Query (caching, polling, refetch on
  focus); client state in Zustand (auth, ephemeral UI).
- **Why**: TanStack Query handles 80% of dashboard state for free. Zustand
  is the smallest viable client-state store. No Redux boilerplate.
- **Consequence**: nothing to migrate later. Adds two small deps.

## 15. shadcn-style component primitives, not a UI kit

- **What**: hand-rolled `Button`, `Card`, `Input`, `Label`, `Badge`, `Table`
  in `frontend/src/components/ui/` using Radix primitives + Tailwind.
- **Why**: matches the requested stack. Components are owned by the project
  so design tweaks don't fight a third-party library's defaults.
- **Consequence**: takes more files than installing one CSS library, but
  copy-paste is the design intent of shadcn.

## 16. `make.py` not `Makefile`

- **What**: Python-based task runner with a `Makefile` shim for Unix.
- **Why**: Windows is the dev environment. `make` isn't natively available;
  `make.py` works on any platform with Python.
- **Consequence**: contributors run `python make.py <cmd>`; the Makefile
  forwards to it.

## 17. Filter `ResourceWarning` from psycopg's `__del__`

- **What**: `pyproject.toml`'s `filterwarnings` ignores `ResourceWarning`
  and `PytestUnraisableExceptionWarning`.
- **Why**: psycopg / SQLAlchemy occasionally GC a connection before the
  cleanup callback runs. The warning is true but cosmetic: the connection is
  closed before the next test fixture runs. With our pool-pre-ping setup
  this never affects correctness.
- **Alternative considered**: hunt every leak source — many are in
  third-party libs (dagster grpc, alembic env imports). Out of scope for
  Stage 1.
- **Consequence**: revisit if we see flaky CI later.

## 18. Removed pre-existing `macro/` venv

- **What**: the project came with an empty `macro/` venv folder. Deleted.
- **Why**: `uv sync` creates `.venv/` (gitignored). Carrying a second
  half-initialised venv invites confusion.
- **Consequence**: anyone with muscle memory pointing at `macro/Scripts/`
  will hit "not found" — README documents the new layout.

## 19. Initial commit on `clean_branch`, never on `main`

- **What**: created `clean_branch` from the GitHub `main`, deleted the
  default `Start` file, and committed Stage 1 there.
- **Why**: the spec mandates `main` stays clean.
- **Consequence**: PRs go from `clean_branch` → `main`. `main` remains the
  release branch.
