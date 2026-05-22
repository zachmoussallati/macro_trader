"""Stage 7 composite scoring layer.

For each (instrument, as_of) the composite emits a single signed score
in ``[-1, 1]`` combining the 10 signal families' outputs, weighted by:

1. Per-(regime, signal) historical Sharpe from
   :mod:`macro_trader.regime.attribution`.
2. Per-row signal confidence on each individual signal value.
3. The BOCPD changepoint probability for the current regime (used
   as a conviction dampener so we don't over-trade through known
   regime transitions).

Three methods register at startup:

- ``composite.linear.v1`` — BASELINE. Linear sum of weight × z ×
  confidence with explicit transition-probability dampening.
- ``composite.bayesian_hier.v1`` — SHADOW. Three-level
  Normal-Inverse-Gamma hierarchy (global / per-regime /
  per-instrument). Refit weekly.
- ``composite.gbm.v1`` — SHADOW, gated on lightgbm. Gradient-boosted
  regression of next-day return on the full signal × regime ×
  instrument feature panel. Refit quarterly.

The :func:`composite.weights.compute_regime_conditional_weights`
function is the load-bearing piece: it turns the attribution table
into per-(regime, signal) weights that all three methods share.
"""

from __future__ import annotations
