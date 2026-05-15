"""Macro factor exposure signal family.

Three methods (Stage 4B):

- ``factor_exposure.ols.v1`` (BASELINE): rolling OLS regression of
  instrument returns on six macro factors. Composite score = -beta @ z.
- ``factor_exposure.rf.v1`` (SHADOW): RandomForestRegressor for
  non-linear / interaction effects. Signal = -predicted_return / vol.
- ``factor_exposure.causal_forest.v1`` (SHADOW, gated on EconML
  install): CausalForestDML for properly identified conditional
  exposures. Signal = -CATE @ z.

Sign convention: positive ``raw_value`` = long bias.
"""
