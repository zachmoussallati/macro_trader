"""Stage 8 portfolio construction layer.

Turns composite scores into sized positions:

- ``portfolio.covariance.*`` — covariance estimation (Ledoit-Wolf
  baseline + DCC-GARCH shadow).
- ``portfolio.construction.*`` — 4 portfolio construction methods
  (ERC baseline + HRP / Black-Litterman / CVaR shadows).
- ``portfolio.drawdown.*`` — equity-curve construction + 3-level
  staged drawdown gates (-5% / -8% / -10%).
- ``portfolio.risk.*`` — per-instrument risk attribution.

All four portfolio methods consume the production composite score
and the production covariance estimate, and respect the current
drawdown gate state. The gate is independent of method — operators
cannot smart-size around a gate.
"""

from __future__ import annotations
