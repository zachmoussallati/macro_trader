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
