"""Drawdown-gate subsystem.

Independent of portfolio method. Each daily run:

1. ``detector`` walks the realised return series, updates the NAV
   peak, and emits the (nav, drawdown_from_peak) timeseries.
2. ``gates`` reads the equity curve + the prior gate state, applies
   the staged trigger logic, and emits a new gate state.
3. ``runner`` glues 1+2 into a single daily job; the position
   runner multiplies its target weights by ``gate.scaling_factor``
   before persisting.
"""

from __future__ import annotations
