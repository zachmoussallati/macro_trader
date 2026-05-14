"""Signal library — Stage 3 onward.

Public surface:

- :class:`SignalMethod`            — abstract base for any swappable signal
- :class:`SignalInput`             — universe + time window + as_of anchor
- :class:`SignalOutput`            — per-instrument output row
- :class:`SignalFamilyComparator`  — base for family-specific comparators

Concrete families live under ``signals.trend``, ``signals.carry``,
``signals.value``. Stage 4 will add ``signals.positioning``,
``signals.dislocation``, ``signals.catalyst``, ``signals.factor_exposure``.
"""

from macro_trader.signals.base import (
    SignalFamilyComparator,
    SignalInput,
    SignalMethod,
    SignalOutput,
)
from macro_trader.signals.decay import rolling_sharpe

__all__ = [
    "SignalFamilyComparator",
    "SignalInput",
    "SignalMethod",
    "SignalOutput",
    "rolling_sharpe",
]
