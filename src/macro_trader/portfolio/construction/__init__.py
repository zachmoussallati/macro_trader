"""Portfolio construction subsystem.

Four methods:

- ``portfolio.erc.v1`` — BASELINE. Equal Risk Contribution; each
  instrument contributes equally to total portfolio variance.
  Robust to estimation error in expected returns (uses covariance
  only). Composite score determines sign; ERC determines magnitude.

- ``portfolio.hrp.v1`` — SHADOW. Hierarchical Risk Parity per Lopez
  de Prado 2016. Tree clustering on the correlation matrix +
  quasi-diagonalisation + recursive bisection. Avoids matrix
  inversion (more robust than ERC under ill-conditioned covariance).

- ``portfolio.black_litterman.v1`` — SHADOW. Bayesian combination
  of an equal-weight prior with composite-score-derived views.
  Posterior expected returns feed a mean-variance optimisation.

- ``portfolio.cvar.v1`` — SHADOW. Mean-CVaR optimisation via the
  Rockafellar-Uryasev LP. Directly minimises the expected loss in
  the worst 5% of cases subject to a composite-derived target
  return.

All four respect:
- ``target_portfolio_vol`` (12% annualised default)
- ``max_block_weight`` (40% per asset class)
- ``max_position_weight`` (15% per instrument)
- ``min_composite_score_threshold`` (skip positions with |score| < 0.10)

The drawdown gate is applied *after* the optimisation by the
runner. Operators cannot smart-size around a gate.
"""

from __future__ import annotations
