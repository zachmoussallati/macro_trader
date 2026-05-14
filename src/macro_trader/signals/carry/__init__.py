"""Carry family — Stage 3 placeholder.

True carry requires futures curve data (Stage 12). Until then this
module produces low-confidence proxies so the framework + dashboard
work end-to-end.
"""

from macro_trader.signals.carry.comparator import CarrySignalComparator
from macro_trader.signals.carry.methods import CarrySpotProxy

__all__ = ["CarrySignalComparator", "CarrySpotProxy"]
