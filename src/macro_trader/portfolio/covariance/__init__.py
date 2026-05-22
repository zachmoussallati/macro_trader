"""Covariance estimation subsystem.

Two methods:

- ``covariance.ledoit_wolf.v1`` — BASELINE. Shrunk sample
  covariance via :class:`sklearn.covariance.LedoitWolf`. Stateless,
  recomputed daily on the trailing 252-day return panel.

- ``covariance.dcc_garch.v1`` — SHADOW. Per-instrument GARCH(1, 1)
  volatilities + Dynamic Conditional Correlation across pairs.
  Gated on the ``arch`` package; weekly refit (Sunday 07:30 UTC).

Both produce ``CovarianceEstimate`` rows (one per pair) plus
``VolatilityEstimate`` rows (one per instrument, annualised).
"""

from __future__ import annotations
