"""Vol surface signal family (Stage 5).

Two methods on the small liquid-options universe (GLD, SLV, USO,
UNG, DBA, SPY — 6 instruments where yfinance options coverage is
reliable):

- ``vol_surface.raw.v1``: IV-RV spread, ATM term-structure slope,
  25-delta skew, vol-of-vol metrics computed directly from raw
  chain quotes.
- ``vol_surface.svi.v1``: cubic-spline-fitted per-slice surface
  with arbitrage checks. The full Gatheral SVI parameterization
  is deferred per the Stage 5 prompt's explicit fallback option
  (see ``notes/stage_5/decisions.md``).

For commodity instruments without reliable yfinance options
coverage (CL/BZ/HG/ALI/PL/ZC/ZS/ZW directly), the methods emit
``confidence=0`` rows so downstream composite scoring ignores them.

**Critical signal metadata flag**: every vol_surface signal output
carries ``historical_backtest_supported: False`` so the Stage 9
backtester skips this family — yfinance only provides current-day
snapshots, no history. The architecture is source-agnostic
(``OptionsChainIngester`` abstract base) so a paid provider with
historical depth can swap in as a config change post-v1.
"""
