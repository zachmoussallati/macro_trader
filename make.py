"""Cross-platform task runner.

Usage:
    python make.py <command> [args]
    python make.py --help
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shlex
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"


# ----------------------------------------------------------------------
# Shell helpers
# ----------------------------------------------------------------------
def _run(
    cmd: str | list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> int:
    """Run a shell command. Returns the exit code."""
    if isinstance(cmd, str):
        display = cmd
        argv = cmd if os.name == "nt" else shlex.split(cmd)
        shell = os.name == "nt"
    else:
        display = " ".join(cmd)
        argv = cmd
        shell = False
    print(f"$ {display}")
    final_env = os.environ.copy()
    if env:
        final_env.update(env)
    proc = subprocess.run(argv, cwd=str(cwd or ROOT), shell=shell, env=final_env)
    if check and proc.returncode != 0:
        raise SystemExit(proc.returncode)
    return proc.returncode


def _uv() -> str:
    uv = shutil.which("uv") or "uv"
    return uv


def _pnpm() -> str:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise SystemExit("pnpm not found on PATH. Install with `npm install -g pnpm`.")
    return pnpm


def _docker_compose_cmd() -> list[str]:
    docker = shutil.which("docker") or "docker"
    return [docker, "compose"]


# ----------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------
def cmd_setup(args: argparse.Namespace) -> None:
    """Install all deps, init DB, create .env."""
    _ensure_env_file()
    print("--- Python deps (uv sync) ---")
    _run([_uv(), "sync", "--extra", "dev"])
    print("--- Frontend deps (pnpm install) ---")
    if (FRONTEND / "package.json").exists():
        _run([_pnpm(), "install"], cwd=FRONTEND)
    else:
        print("(skipped: frontend/package.json not found)")
    print("--- Bringing up Docker services ---")
    _run([*_docker_compose_cmd(), "up", "-d"])
    print("--- Initializing DB ---")
    _run([_uv(), "run", "python", "-m", "scripts.setup_db"])
    print("Setup complete. Next: `python make.py dev-all`")


def cmd_dev_backend(args: argparse.Namespace) -> None:
    _run(
        [_uv(), "run", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
    )


def cmd_dev_dagster(args: argparse.Namespace) -> None:
    env = {"DAGSTER_HOME": str(ROOT / "dagster_home")}
    (ROOT / "dagster_home").mkdir(exist_ok=True)
    _run(
        [_uv(), "run", "dagster", "dev", "-f", "orchestration/definitions.py", "-p", "3000"],
        env=env,
    )


def cmd_dev_frontend(args: argparse.Namespace) -> None:
    # Call vite directly via node_modules/.bin to bypass pnpm's auto-install
    # check (it exits non-zero on the harmless ERR_PNPM_IGNORED_BUILDS warning).
    binname = "vite.cmd" if os.name == "nt" else "vite"
    vite = FRONTEND / "node_modules" / ".bin" / binname
    if not vite.exists():
        _run([_pnpm(), "install"], cwd=FRONTEND, check=False)
    _run([str(vite)], cwd=FRONTEND)


def cmd_dev_all(args: argparse.Namespace) -> None:
    """Start FastAPI + Dagster + frontend concurrently."""
    print("Starting backend, dagster, and frontend in parallel.")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
        ex.submit(cmd_dev_backend, args)
        ex.submit(cmd_dev_dagster, args)
        ex.submit(cmd_dev_frontend, args)


def cmd_test(args: argparse.Namespace) -> None:
    cmd_test_backend(args)
    cmd_test_frontend(args)


def cmd_test_backend(args: argparse.Namespace) -> None:
    extra = list(args.extra) if args.extra else []
    _run([_uv(), "run", "pytest", *extra])


def cmd_test_frontend(args: argparse.Namespace) -> None:
    if not (FRONTEND / "package.json").exists():
        print("(skipped: frontend not initialised)")
        return
    # Call vitest directly via node_modules/.bin to bypass pnpm's auto-install
    # behaviour (which exits non-zero on ignored-build warnings).
    binname = "vitest.cmd" if os.name == "nt" else "vitest"
    vitest = FRONTEND / "node_modules" / ".bin" / binname
    if not vitest.exists():
        # Fall back to pnpm if deps weren't installed yet.
        _run([_pnpm(), "install"], cwd=FRONTEND, check=False)
    _run([str(vitest), "run"], cwd=FRONTEND)


def cmd_lint(args: argparse.Namespace) -> None:
    _run([_uv(), "run", "ruff", "check", "."])
    _run([_uv(), "run", "ruff", "format", "--check", "."])
    _run([_uv(), "run", "mypy", "src/macro_trader", "api", "orchestration"])
    if (FRONTEND / "package.json").exists():
        _run([_pnpm(), "lint"], cwd=FRONTEND, check=False)


def cmd_format(args: argparse.Namespace) -> None:
    _run([_uv(), "run", "ruff", "format", "."])
    _run([_uv(), "run", "ruff", "check", "--fix", "."])


def cmd_db_up(args: argparse.Namespace) -> None:
    _run([*_docker_compose_cmd(), "up", "-d", "postgres", "pgadmin"])


def cmd_db_down(args: argparse.Namespace) -> None:
    _run([*_docker_compose_cmd(), "down"])


def cmd_db_reset(args: argparse.Namespace) -> None:
    extra = ["--yes"] if args.yes else []
    _run([_uv(), "run", "python", "-m", "scripts.reset_db", *extra])


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _ensure_env_file() -> None:
    env = ROOT / ".env"
    if env.exists():
        return
    sample = ROOT / ".env.example"
    if not sample.exists():
        return
    env.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
    print("Created .env from .env.example. EDIT IT before running `dev-all`.")


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Macro Trader task runner.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="Install deps, init DB, create .env").set_defaults(func=cmd_setup)
    sub.add_parser("dev-backend", help="Run FastAPI dev server").set_defaults(func=cmd_dev_backend)
    sub.add_parser("dev-dagster", help="Run Dagster dev").set_defaults(func=cmd_dev_dagster)
    sub.add_parser("dev-frontend", help="Run Vite dev server").set_defaults(func=cmd_dev_frontend)
    sub.add_parser("dev-all", help="FastAPI + Dagster + frontend").set_defaults(func=cmd_dev_all)

    test_p = sub.add_parser("test", help="Run all tests")
    test_p.add_argument("extra", nargs=argparse.REMAINDER)
    test_p.set_defaults(func=cmd_test)

    test_be = sub.add_parser("test-backend", help="Run backend pytest")
    test_be.add_argument("extra", nargs=argparse.REMAINDER)
    test_be.set_defaults(func=cmd_test_backend)

    sub.add_parser("test-frontend", help="Run frontend vitest").set_defaults(func=cmd_test_frontend)

    sub.add_parser("lint", help="Ruff + mypy + eslint").set_defaults(func=cmd_lint)
    sub.add_parser("format", help="Ruff format + fix").set_defaults(func=cmd_format)

    sub.add_parser("db-up", help="Start Postgres + pgAdmin").set_defaults(func=cmd_db_up)
    sub.add_parser("db-down", help="Stop docker services").set_defaults(func=cmd_db_down)

    db_reset = sub.add_parser("db-reset", help="Drop + recreate DB")
    db_reset.add_argument("--yes", action="store_true", help="skip confirmation")
    db_reset.set_defaults(func=cmd_db_reset)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
