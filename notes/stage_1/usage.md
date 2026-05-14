# Stage 1 — usage

How to drive what's been built.

## First-time setup

Prereqs already installed on a Windows dev box during Stage 1:
- `uv` (via `winget install astral-sh.uv`)
- Node LTS (via `winget install OpenJS.NodeJS.LTS`)
- `pnpm` (via `npm install -g pnpm`)
- Docker Desktop

On a fresh clone:

```powershell
git clone <repo> macro_trader
Set-Location macro_trader
git checkout clean_branch
python make.py setup
```

`python make.py setup` does:

1. Copy `.env.example` → `.env` if `.env` doesn't exist.
2. `uv sync --extra dev` — install Python deps.
3. `pnpm install` in `frontend/` — install JS deps.
4. `docker compose up -d` — start Postgres + pgAdmin.
5. `python -m scripts.setup_db` — create main + test DBs, run Alembic
   migrations, seed the admin user.

After that, you can edit `.env` (especially `JWT_SECRET_KEY`,
`ADMIN_PASSWORD`) before first running services.

## Daily dev loop

```powershell
python make.py dev-all      # all three services in parallel
```

This starts:
- **FastAPI** on `http://localhost:8000` (with `/docs`, `/redoc`).
- **Dagster** on `http://localhost:3000`.
- **Vite dev server** on `http://localhost:5173`.

Each can also be run individually:
- `python make.py dev-backend`
- `python make.py dev-dagster`
- `python make.py dev-frontend`

## Tests

```powershell
python make.py test           # everything
python make.py test-backend   # pytest
python make.py test-frontend  # vitest

# pass extra args after `--`:
python make.py test-backend -- -k methods -x
```

Backend tests skip the integration suite if Postgres isn't reachable.
Frontend tests don't require any services.

## Lint / format

```powershell
python make.py lint       # ruff check + ruff format --check + mypy + eslint
python make.py format     # ruff format + ruff check --fix
```

## DB operations

```powershell
python make.py db-up                  # start docker services
python make.py db-down                # stop them
python make.py db-reset --yes         # DESTRUCTIVE: drop+recreate main DB
```

Manual psql:

```powershell
docker exec -it macro_trader_postgres psql -U macro -d macro_trader
```

## Verifying everything works

After `python make.py dev-all` is running:

```powershell
# 1) Health endpoint reports all subsystems healthy:
curl http://localhost:8000/api/v1/health

# 2) Empty methods registry (Stage 1 has no methods):
curl http://localhost:8000/api/v1/methods       # → []

# 3) Login (admin seeded from .env):
curl -X POST http://localhost:8000/api/v1/auth/login `
  -H "Content-Type: application/json" `
  -d '{"email":"admin@example.com","password":"change_me_dev_admin_password"}'

# 4) Frontend: open http://localhost:5173 in a browser. Sign in. /methods
#    should render the empty state with no error.

# 5) Dagster: open http://localhost:3000. Materialise `heartbeat_asset`
#    once manually; verify a row appears via psql:
docker exec macro_trader_postgres psql -U macro -d macro_trader -c `
  "SELECT timestamp, source FROM system.heartbeat ORDER BY id DESC LIMIT 1;"
```

## Using the methods framework in code

In Stage 1 there's nothing to register — but the API is ready:

```python
from macro_trader.methods import (
    Method, MethodMetadata, MethodStatus,
    register_method, list_methods, set_status,
    get_production, get_shadows,
)
from macro_trader.db.engine import get_session

class MyBaseline(Method[MyInput, MyOutput]):
    metadata = MethodMetadata(
        method_id="data_quality.zscore.v1",
        component="data_quality",
        name="Z-score outlier flag",
        version="1.0.0",
        description="Flag |z| > 3 over a 60-day rolling window.",
    )
    def fit(self, data): ...
    def predict(self, data): ...
    def serialize(self): ...
    @classmethod
    def deserialize(cls, blob): ...

with get_session() as session:
    register_method(MyBaseline(), MethodStatus.BASELINE,
                    session=session, reason="Stage 2 — first data quality method")

# Later, get whichever method drives data quality decisions:
driver = get_production("data_quality")
flags = driver.predict(today_data)

# And iterate shadows for comparison:
for shadow in get_shadows("data_quality"):
    shadow_flags = shadow.predict(today_data)
    # ... feed to a MethodComparator subclass
```

## Admin promotion via API

```bash
# log in as admin → get access_token
ACCESS=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"..."}' \
  | jq -r .access_token)

# review evidence first:
curl http://localhost:8000/api/v1/methods/data_quality.isoforest.v1 | jq
curl "http://localhost:8000/api/v1/methods/comparisons?component=data_quality" | jq

# then promote:
curl -X POST http://localhost:8000/api/v1/methods/data_quality.isoforest.v1/status \
  -H "Authorization: Bearer $ACCESS" \
  -H "Content-Type: application/json" \
  -d '{"status":"production","reason":"30-day shadow shows 12% improvement on outlier_recall"}'
```

The registry automatically demotes the previous production / baseline
method for that component to `deprecated`.

## Inspecting the in-memory registry from a script

```powershell
uv run python -c "from macro_trader.methods import list_methods; print(list_methods())"
```

Returns the contents of the *process* registry (empty in a fresh shell —
methods must be registered by code that runs in the same process). The
durable mirror is `system.methods_registry` in Postgres.

## Common operational commands

| Need | Command |
| --- | --- |
| Tail Postgres logs | `docker logs -f macro_trader_postgres` |
| pgAdmin UI | `http://localhost:5050` (admin@example.com / from .env) |
| Manual `alembic upgrade` | `uv run alembic upgrade head` |
| Generate new migration | `uv run alembic revision --autogenerate -m "..."` |
| Restart just FastAPI | Ctrl-C the dev-all and re-run `python make.py dev-backend` |
