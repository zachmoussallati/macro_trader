"""Cross-asset dislocation signal family.

- ``dislocation.pca.v1`` (BASELINE): residual to top-K PCA factor
  reconstruction over a rolling 252-day return panel. Signed so
  positive raw_value = long bias.
- ``dislocation.dfm.v1`` (SHADOW): Dynamic Factor Model with Kalman-
  filtered time-varying loadings. Same conceptual signal as PCA;
  captures regime-dependent shifts.
"""
