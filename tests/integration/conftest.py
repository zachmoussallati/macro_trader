"""Integration-test conftest.

Re-exports the ``backfill_panel`` fixture from
``tests.integration.fixtures.backfill`` so tests can request it via
``def test_x(backfill_panel)`` without an explicit import.
"""

from tests.integration.fixtures.backfill import backfill_panel  # noqa: F401
