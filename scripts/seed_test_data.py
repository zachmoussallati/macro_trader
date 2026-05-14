"""Seed minimal test data — Stage 1 only seeds an admin user.

Later stages will add reference instruments, macro series, etc.
"""

from __future__ import annotations

from scripts.setup_db import seed_admin_user


def main() -> None:
    seed_admin_user()
    print("Seed complete.")


if __name__ == "__main__":
    main()
