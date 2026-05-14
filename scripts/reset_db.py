"""Drop and recreate the main DB. Destructive — confirm before running."""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, text

from macro_trader.config import get_settings
from macro_trader.logging_setup import get_logger
from scripts.setup_db import run_alembic_upgrade, seed_admin_user


def main() -> None:
    log = get_logger("scripts.reset_db")
    settings = get_settings()

    if "--yes" not in sys.argv:
        answer = input(f"This will DROP {settings.database.name!r}. Type 'yes': ")
        if answer.strip().lower() != "yes":
            print("Aborted.")
            return

    admin_url = (
        f"postgresql+psycopg://{settings.database.user}:{settings.database.password}"
        f"@{settings.database.host}:{settings.database.port}/postgres"
    )
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        # Kick any open sessions.
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": settings.database.name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{settings.database.name}"'))
        conn.execute(text(f'CREATE DATABASE "{settings.database.name}"'))
        log.info("scripts.reset_db.recreated", database=settings.database.name)

    run_alembic_upgrade()
    seed_admin_user()
    print("DB reset complete.")


if __name__ == "__main__":
    main()
