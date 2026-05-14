"""Calendar layer — event ingestion, linkage, API."""

from macro_trader.calendar.api import events_in_window, is_blackout, next_event

__all__ = ["events_in_window", "is_blackout", "next_event"]
