"""Nowcasting signal family (Stage 5).

For high-impact macro releases (NFP, CPI, ISM, retail sales, GDP,
EIA petroleum status), forecast the release using lead indicators
already in the system. Signal = nowcast surprise (predicted -
consensus, z-scored), mapped to the release's affected instruments.

Two methods:

- ``nowcasting.ols_ar.v1`` (BASELINE): linear regression of release
  on lagged release + lead indicators.
- ``nowcasting.bvar.v1`` (SHADOW): Bayesian-ridge variant with
  Normal-Inverse-Gamma prior centred on the random-walk
  specification (own-lag shrunk to 1, lead-indicator coefficients
  shrunk to 0). The analytical posterior gives a closed-form
  point estimate + uncertainty.
"""
