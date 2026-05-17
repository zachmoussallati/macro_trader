"""Regime classifier (Stage 6).

The first system layer that operates over signals rather than
producing them. Five methods classify the macro environment into
K=5 named regimes:

- ``risk_on_growth`` — strong growth, low vol, positive sentiment
- ``risk_off_defensive`` — growth slowing, vol elevated, USD bid
- ``stagflation`` — inflation rising, growth weak
- ``carry_friendly`` — vol low, rates stable, USD weak/stable
- ``vol_spike`` — realized + implied vol both elevated

Methods:

- ``regime.rules.v1`` BASELINE — transparent decision-tree rules
- ``regime.gmm.v1`` SHADOW — Gaussian Mixture Model, weekly refit
- ``regime.hmm.v1`` SHADOW — Hidden Markov Model, quarterly refit
- ``regime.msvar.v1`` SHADOW — Markov-Switching regression, quarterly refit
- ``regime.bocpd.v1`` SHADOW — Bayesian Online Changepoint Detection

Output: discrete label PLUS full probability vector. Stage 7's
composite scoring weights signals by regime via the attribution
table populated by ``regime.attribution``.
"""

NAMED_REGIMES: tuple[str, ...] = (
    "risk_on_growth",
    "risk_off_defensive",
    "stagflation",
    "carry_friendly",
    "vol_spike",
)

