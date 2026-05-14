# Stage 1 — tradeoffs and deferrals

What was deliberately deferred or skipped, and why.

## Deferred

### Test database for unit tests
Unit tests for the methods framework do NOT touch the DB; they use a
fresh in-memory `MethodRegistry`. Integration tests do hit the DB but
auto-skip if Postgres isn't reachable. This means unit tests stay fast
(under 5s) and don't require Docker in the dev loop. Trade: any DB-related
bug in `_db_upsert` / `_db_update_status` is only caught by integration
tests.

### Frontend e2e tests
No Playwright / Cypress in Stage 1. Vitest covers component smoke tests
only. Once we have a real auth-protected dashboard with actual data in
Stage 11, we'll add Playwright.

### Type-strict mypy on `api/`, `orchestration/`
`pyproject.toml` enables strict mypy globally, but the FastAPI dependency
machinery and Dagster decorators are hostile to strict typing at module
level. We accept that the `[[tool.mypy.overrides]]` for tests is the only
relaxation today; we'll selectively narrow more as patterns settle.

### Pre-commit hooks not auto-installed
`pre-commit` is in dev deps but the `.pre-commit-config.yaml` and
`pre-commit install` are deferred to Stage 2 — we want a stable codebase
before locking down formatting on every commit. Until then, `python
make.py lint` and `format` are the manual gates.

### Method serialization not wired to DB
`Method.serialize()` / `deserialize()` are abstract; the registry has a
`serialized_blob` column ready to receive them. Actually round-tripping
sklearn-style estimators through the DB is Stage 3's job (when the first
real fittable method exists). Keeping the interface present but unused
documents the intent.

### Alembic autogenerate compare-by-default
We didn't add a CI step that runs `alembic check` to fail if model
metadata drifts from migrations. Add this when we have more than one
migration to compare against (Stage 2).

### TimescaleDB hypertables
The extension is enabled, but no `SELECT create_hypertable(...)` yet. Stage
2 will hypertable the time-series tables (`market_data.*`,
`positioning.*`).

### Refresh-token revocation list / DB cleanup job
Old / used refresh tokens are kept in the table indefinitely with
`revoked = true`. A small cleanup task (Dagster sensor or cron) belongs in
Stage 2 once we have the orchestration pattern set.

### Rate limiting, CSRF, security headers
Auth is JWT-bearer over CORS. No rate limit on `/auth/login`. Acceptable
for local dev; production rollout needs nginx-level / API-gateway
controls. Out of scope for foundation.

### Per-domain Alembic versions
The spec mentions "Alembic configured for migrations with
`version_table_schema` per domain schema". We use a single
`alembic_version` table in `system` because per-schema versioning is a
maintenance burden and we don't need to migrate one schema independently
of the rest yet. Revisit if/when we ship a schema-specific service.

### Concrete Method implementations
Stage 1 ships ZERO `Method` subclasses in production code. The `Identity`
/ `NoisyIdentity` test doubles live only in `tests/`. This is intentional
— foundation only. Stage 2 adds the first real method (data quality:
z-score baseline vs Isolation Forest enhancement).

### Frontend: no real-time WebSocket updates
The methods page polls via TanStack Query refetch. Live updates are a
Stage 11 concern when we have streaming data to show.

### CI for the methods framework only — not the full health endpoint
Backend CI runs unit + integration tests against a Postgres service
container, which exercises the entire layer including
`/api/v1/methods`. We don't ship a separate "API live" black-box check —
that arrives when there's a deployed environment to point at.

## Not done

### Setup of any external data sources
`config.data_sources` is a placeholder. No FRED / Alpha Vantage / Quandl
API keys are required (or used) in Stage 1. Stage 2 will add the actual
fetchers and the corresponding scheduled assets.

### Authentication on the heartbeat / Dagster UI
Dagster's UI is on 3000 with no auth. Same for pgAdmin on 5050.
Acceptable for local dev. Future production: put both behind an SSO
proxy.

### Custom OAuth providers
JWT-only. No "sign in with Google". The single seeded admin user is
sufficient for an internal tool right now.

### Storage of fitted models outside Postgres
Big serialised models can blow up DB row size. For Stage 1, the
`serialized_blob` column is `BYTEA` with no size cap — adequate for the
trivial test methods. Stage 3+ will likely add an S3-backed alternative
for anything > 1 MB; the registry would store a URI instead of a blob.

### Component-specific comparators
We ship the `MethodComparator` ABC and an `IdentityComparator` in tests.
No concrete production comparator exists because no production methods
exist. Per the framework design, every component that introduces an
enhancement also introduces its component-specific comparator.
