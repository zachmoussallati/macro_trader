"""Positioning signal family (CFTC COT-derived signals).

- ``positioning.cot_zscore.v1`` (BASELINE): rolling z-score of managed-money
  net positioning from the disaggregated COT report, sign-inverted so
  extreme net long -> contrarian short.
- ``positioning.cot_commercial.v1`` (SHADOW): commercial net positioning
  extremes from the legacy report; smart-money proxy.
"""
