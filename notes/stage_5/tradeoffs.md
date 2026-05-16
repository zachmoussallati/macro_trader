# Stage 5 — tradeoffs and deferred work

## 1. Paid options data source (post-v1)

- **What's deferred**: yfinance has no historical chains; every
  vol-surface signal emits `historical_backtest_supported: False`.
  Promotion of `vol_surface.svi.v1` to PRODUCTION is structurally
  blocked until paid data lands.
- **Cheapest options**:
  - **Polygon.io basic options** — $29/mo, full historical, 15-min
    delayed real-time, 5 calls/sec rate limit. Best price/feature.
  - **Alpha Vantage premium** — $50/mo, historical EOD + some
    intraday. Stable API.
  - **ORATS one-shot** — pay-per-snapshot for historical backfill,
    monthly subscription for live. Most expensive but best data
    quality for vol-surface work.
- **Swap path**: add `signals/vol_surface/ingestion/polygon.py`
  (or `alpha_vantage.py` / `orats.py`) subclassing
  `OptionsChainIngester`; set `signals.vol_surface.data_source` in
  `config/base.yaml`. No other code changes.

## 2. Full Gatheral SVI (post-paid-data)

- **What's deferred**: Stage 5 ships spline interpolation per-slice
  with calendar arb check. Full 5-parameter SVI parameterization
  (`w(k) = a + b*(rho*(k-m) + sqrt((k-m)² + sigma²))`) sits
  behind a `# TODO post-v1` in `notes/stage_5/decisions.md` §4.
- **Why deferred now**: full SVI needs reliable initial guesses
  (Quasi-Explicit SVI of De Marco & Martini 2009), constrained
  optimization (`scipy.optimize.minimize` with `method='SLSQP'`),
  and deeper strike coverage than yfinance reliably provides for
  commodity ETFs (typically 5-10 strikes per expiry for UNG/DBA;
  Polygon would deliver 30-50). The spline fallback is
  appropriate for the data quality.

## 3. Vol-surface dashboard page (post-v1)

- **What's deferred**: dedicated `/signals/vol_surface` page with
  3D Plotly surface viz. The Stage 4C frontend pattern (TanStack
  Query + Recharts table) doesn't accommodate 3D well; the page
  needs explicit Plotly integration which is fundamentally
  different from the rest of the dashboard.
- **What's shipped**: backend API endpoints (would be added in a
  Stage 5B follow-up), `vol_surface_signal` in the heatmap's
  `ALL_COMPONENTS` so the column auto-appears.
- **Stage 5B / 6 frontend pickup**: build `/signals/vol_surface`,
  `/signals/nowcasting`, and `/signals/alt_data` dedicated pages
  following the Stage 4C `SignalsPositioning`/`SignalsDislocation`
  pattern + Plotly 3D for vol surface.

## 4. Nowcasting consensus values

- **What's deferred**: Stage 2's calendar ingest doesn't reliably
  populate `consensus` fields in `metadata`. The nowcasting
  surprise computation currently uses
  `(predicted - last_actual)` as a proxy when consensus is
  unavailable.
- **Effect**: surprise is computed against the prior period's
  release, not the market's pre-release expectation. Works for
  AR-1 like processes (NFP, CPI) where lagged value approximates
  the expected baseline; less useful for highly mean-reverting
  releases like ISM PMI.
- **Stage 6 / calendar pass**: extend Stage 2's calendar
  ingestion to populate consensus from a paid source
  (TradingEconomics / Bloomberg) or from a free aggregate like
  `econoday.com`. Documented as a calendar-ingest enhancement.

## 5. BS pricing uses constant `r`, `q`

- **What's deferred**: pricing uses `r = 0.045`, `q = 0.0` for
  Black-Scholes IV recovery + Greeks. SOFR daily series isn't
  pulled into the pricing module, and ETF dividends are ignored.
- **Effect**: IV recovered from quotes is biased by ~0.5-1% in
  absolute terms when actual `r` diverges from 4.5% (e.g.
  zero-rate years). For high-delta in-the-money options the bias
  is larger because the rate sensitivity grows with intrinsic
  value.
- **Stage 6 refinement**: pull SOFR daily from FRED (already
  ingested in Stage 2) and pass it through to the pricing module.
  ETF dividend yields are sparser; defer to the same paid-data
  pass that brings options history.

## 6. Real-data integration tests (carried from Stage 4C)

- **What's deferred**: tests gated on `@pytest.mark.real_data`
  remain skipped because the backfill cache
  (`tests/data/backfill_504d.parquet`) wasn't generated this
  session (FRED_API_KEY not set in this shell).
- **Stage 6 housekeeping**: regenerate the cache + run
  `pytest -m real_data` once per environment. Add equivalent
  real-data smoke tests for the three new families (vol_surface,
  nowcasting, alt_data).

## 7. No alt-data comparator

- **What**: Per Stage 5 prompt's explicit instruction. Each
  alt-data signal targets a different sub-universe, so a
  cross-method comparator would compare apples to oranges.
- **Composite handling**: Stage 7 (signal combination) will weight
  alt-data signals via the standard z * confidence aggregation.
  Uncovered instruments have `confidence=0` so they're ignored
  by definition.

## 8. Calendar arbitrage in vol surface: detected, not corrected

- **What**: `check_calendar_arbitrage()` flags slices where total
  variance decreases as expiry grows. Stage 5 surfaces the
  violation count in metadata but doesn't re-fit to enforce
  monotonicity.
- **Stage 6+ correction path**: when paid data lands, switch to
  full SVI and use Gatheral & Jacquier (2014)'s joint
  arbitrage-free surface fit. Spline-based correction is too
  brittle to attempt with only 5-10 strikes per slice.

## 9. Stage 5 deferred deliverables called out in the prompt

- 3 dedicated frontend pages (per §3 above).
- Real-data integration tests for the new families (per §6).
- Vol-surface dashboard's 3D Plotly viz (per §3).
- API endpoints for vol_surface/slices, vol_surface/term_structure,
  nowcasting/projections, nowcasting/history, alt_data/components.
  Backend works without them (the heatmap pulls from the existing
  `signals/heatmap` endpoint); dedicated endpoints are needed for
  the deferred dashboard pages.

## 10. Stage 5 unit tests not run end-to-end against integration
  fixtures

- **What**: 35 new unit tests (alt_data 5, nowcasting 10,
  vol_surface 20) pass. Full integration tests through Postgres
  weren't run this turn (the harness shell lost Postgres
  connectivity mid-session — alembic upgrade timed out).
- **Stage 6 housekeeping**: run `python make.py test` against a
  live Postgres + EconML environment to validate the full
  pipeline end-to-end.
