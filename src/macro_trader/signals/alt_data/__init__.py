"""Alt-data signal family (Stage 5).

Three signals derived from already-ingested Stage 2 data:

- ``alt_data.eia_storage.v1``: weekly storage surprise vs 5-year
  seasonal average. Covers CL / BZ (crude inventory) and NG (natural
  gas inventory).
- ``alt_data.usda_wasde.v1``: monthly WASDE surprise via month-over-
  month change in production/yield. Covers ZC / ZS / ZW.
- ``alt_data.google_trends.v1``: contrarian search-interest sentiment
  composite. Covers commodities with mapped queries (CL / BZ / GC /
  HG / NG).

Sign convention: positive raw_value = long bias (same as every other
family). EIA + USDA surprises invert: positive supply surprise ->
bearish -> negative raw_value. Google Trends inverts: high attention
-> contrarian short -> negative raw_value.

No comparator (per Stage 5 prompt — each signal targets a different
universe, so "method A vs method B" isn't meaningful). Composite
scoring in Stage 7 will combine them via standard z * confidence.
"""
