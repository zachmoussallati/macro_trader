"""structlog configuration.

- ``console`` format: human-friendly, coloured, used in dev.
- ``json`` format: one JSON object per line, used in prod.

The same context-binding API is used everywhere so call sites don't care
about the rendering. We use ``structlog.contextvars`` so log fields (e.g.
request_id) propagate across ``await`` boundaries.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from macro_trader.config import get_settings

_configured = False


def configure_logging(*, force: bool = False) -> None:
    """Install structlog + stdlib logging configuration. Idempotent."""
    global _configured
    if _configured and not force:
        return

    settings = get_settings()
    level_name = (settings.logging.level or settings.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt = settings.logging.format or settings.log_format or "console"

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer: Processor = structlog.processors.JSONRenderer(sort_keys=True)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Make stdlib logging go through structlog too, so libraries (sqlalchemy,
    # uvicorn) get the same formatting.
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
        force=True,
    )

    _configured = True


def get_logger(name: str | None = None, **initial_context: Any) -> Any:
    """Return a bound structlog logger. Configures logging on first call."""
    if not _configured:
        configure_logging()
    logger = structlog.get_logger(name)
    if initial_context:
        logger = logger.bind(**initial_context)
    return logger
