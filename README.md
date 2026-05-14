# Macro Trader

Systematic macro trading system — Stage 1 (Foundation).

See `docs/architecture.md` for the high-level design and `docs/stages.md` for the
full 13-stage build plan. Stage-by-stage notes live in `notes/`.

## Quickstart

```powershell
# Prereqs: Python 3.12, uv, Node 20+, pnpm, Docker Desktop
python make.py setup      # install deps, init DB, create .env
python make.py dev-all    # FastAPI + Dagster + frontend
```

See `docs/runbook.md` for operational details.
