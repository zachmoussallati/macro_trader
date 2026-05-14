# Macro Trader

A systematic macro trading system for commodity futures (energy, base metals,
precious metals, agriculture), built up across 13 stages. **Stage 1
(Foundation)** is what's in this repo today: project skeleton, infrastructure,
and — most importantly — the **methods comparison framework** that every
later stage uses to compare baseline algorithms against enhancements.

## Stage status

| Stage | State |
| --- | --- |
| 1. Foundation | ✅ this commit — see `notes/stage_1/` |
| 2. Data layer | next |
| 3–13 | not started — see `docs/stages.md` |

## What's here

- **`src/macro_trader/methods/`** — the comparison framework. `Method`
  abstract base, `MethodStatus` lifecycle (development → baseline → shadow →
  production → deprecated), `MethodRegistry` (DB-mirrored, thread-safe),
  `MethodComparator` + `ComparisonResult`, generic agreement / stability
  metrics, and `PromotionCriteria` + `evaluate_promotion` (never
  auto-promotes). **Critical**: read
  [`docs/methods_framework.md`](docs/methods_framework.md) before adding
  algorithms in Stage 3+.
- **`api/`** — FastAPI app. `/api/v1/health`, JWT auth (register / login /
  refresh / me), and `/api/v1/methods` (registry + comparisons + admin status
  change).
- **`orchestration/`** — Dagster project with a `heartbeat_asset` (proves
  orchestrator + DB connectivity) on a 5-minute schedule.
- **`frontend/`** — React + Vite + TypeScript + Tailwind + shadcn-style
  primitives. Routes: `/`, `/login`, `/methods` (protected).
- **`config/`** — layered YAML config (`base.yaml`, `dev.yaml`, `test.yaml`,
  `prod.yaml`) merged with `.env` via `pydantic-settings`.
- **`docker-compose.yml`** — Postgres 16 + TimescaleDB + pgAdmin.
- **`alembic/`** — DB migrations. `0001` creates all domain schemas plus the
  `system.methods_registry`, `method_status_history`, `method_comparisons`,
  `heartbeat`, and `auth.{users,refresh_tokens}` tables.

## Quickstart

```powershell
# Prereqs (one-time): uv, Node 20+, pnpm, Docker Desktop.
python make.py setup                  # installs deps, brings up Postgres, runs migrations, seeds admin
python make.py dev-all                # FastAPI :8000, Dagster :3000, frontend :5173
```

Then:

- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/api/v1/health>
- Dagster: <http://localhost:3000>
- Frontend: <http://localhost:5173>

## Tests

```powershell
python make.py test         # both Python and frontend
python make.py test-backend # pytest only
python make.py test-frontend # vitest only
```

Backend: 33 tests (24 unit + 9 integration). Integration tests skip
automatically if Postgres is unreachable.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — high-level layered design.
- [`docs/stages.md`](docs/stages.md) — the 13-stage plan.
- [`docs/methods_framework.md`](docs/methods_framework.md) — **read this
  before Stage 3+ work**.
- [`docs/runbook.md`](docs/runbook.md) — operational procedures.
- [`notes/stage_1/`](notes/stage_1/) — Stage 1 decisions, tradeoffs, usage.

## Project layout

```
macro_trader/
├── api/                      FastAPI app
├── alembic/                  DB migrations
├── config/                   Layered YAML config
├── docs/                     Architecture, stages, methods framework, runbook
├── frontend/                 React/Vite/TS dashboard
├── notes/                    Stage-by-stage decision log
├── orchestration/            Dagster definitions + assets
├── scripts/                  setup_db, reset_db, seed_test_data
├── src/macro_trader/
│   ├── methods/              Cross-cutting framework (THE thing in Stage 1)
│   ├── db/                   SQLAlchemy 2.x engine, base, models, schemas
│   ├── data, signals, regime, portfolio, backtest,
│   │ claude_layer, calendar, execution   (empty packages; populated in later stages)
│   └── utils/                dates, types
├── tests/                    pytest unit + integration
├── pyproject.toml            uv-managed deps + ruff + mypy + pytest config
├── docker-compose.yml        Postgres + TimescaleDB + pgAdmin
└── make.py                   cross-platform task runner
```

## License

Proprietary.
