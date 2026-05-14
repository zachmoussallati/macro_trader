"""One-shot DB setup.

Creates the test database if missing, runs alembic upgrade head against the
main DB, and seeds the admin user.

Idempotent: re-running is safe.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from macro_trader.config import get_settings
from macro_trader.db.engine import get_session
from macro_trader.db.models.auth import User, UserRole
from macro_trader.logging_setup import get_logger
from macro_trader.utils.dates import utcnow

ROOT = Path(__file__).resolve().parents[1]


def ensure_databases() -> None:
    """Create the main and test databases if they don't exist yet."""
    log = get_logger("scripts.setup_db")
    settings = get_settings()

    # Connect to the default `postgres` database to be able to CREATE DATABASE.
    admin_url = (
        f"postgresql+psycopg://{settings.database.user}:{settings.database.password}"
        f"@{settings.database.host}:{settings.database.port}/postgres"
    )
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        for db_name in (settings.database.name, settings.database.test_name):
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
            ).first()
            if exists is None:
                log.info("scripts.setup_db.create_database", database=db_name)
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            else:
                log.info("scripts.setup_db.database_exists", database=db_name)


def run_alembic_upgrade() -> None:
    """Run alembic upgrade head against the main DB."""
    log = get_logger("scripts.setup_db")
    log.info("scripts.setup_db.alembic_upgrade")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"alembic failed with code {result.returncode}")


def seed_admin_user() -> None:
    """Create the admin user from `.env` credentials if it doesn't exist."""
    from sqlalchemy import select

    from api.auth.jwt import hash_password

    log = get_logger("scripts.setup_db")
    settings = get_settings()
    with get_session() as session:
        existing = session.scalar(
            select(User).where(User.email == settings.auth.admin_email)
        )
        if existing is not None:
            log.info("scripts.setup_db.admin_exists", email=settings.auth.admin_email)
            return
        user = User(
            email=settings.auth.admin_email,
            hashed_password=hash_password(settings.auth.admin_password),
            role=UserRole.ADMIN,
            is_active=True,
            is_superuser=True,
            created_at=utcnow(),
        )
        session.add(user)
        log.info("scripts.setup_db.admin_created", email=settings.auth.admin_email)


def main() -> None:
    ensure_databases()
    run_alembic_upgrade()
    seed_admin_user()
    print("DB setup complete.")


if __name__ == "__main__":
    main()
