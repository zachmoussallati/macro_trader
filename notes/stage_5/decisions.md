# Stage 5 — decisions

## 1. Phase 0 verification — three deferrals carried forward

- **0.1 Vitest tests**: Not executed (Node / pnpm not on PATH in this
  harness shell). The four Stage 4C smoke tests + the new ones
  remain unverified locally; user runs `pnpm test` to validate.
- **0.2 Backfill cache**: Not regenerated (FRED_API_KEY not set in
  this shell). The five `real_data`-marked tests skip cleanly with
  the standard "backfill cache not found" message.
- **0.3 Real-data CATE tests**: Already shipped in Stage 5 commit
  `4162fb3` — two test files (`test_factor_exposure_cf_real.py`,
  `test_catalyst_causal_real.py`) gated on `@pytest.mark.real_data`.

These three items are documented as Stage 6 housekeeping in `next.md`.

## 2. Vol surface data source: yfinance (pre-decided in prompt)

- **What**: Stage 5 uses yfinance only for options data. No paid
  provider.
- **Implications acknowledged**:
  - No historical surface data; vol-surface signals live-only.
  - Every output's metadata carries `historical_backtest_supported: False`
    and `data_source: "yfinance"`.
  - Stage 9 backtester will read these flags and skip the
    vol_surface family entirely.
  - SHADOW promotion of `vol_surface.svi.v1` is blocked until
    historical comparison data exists (post-paid-source).
- **Architectural payoff**: `OptionsChainIngester` abstract base in
  `signals/vol_surface/ingestion/base.py`. The `YfinanceOptionsIngester`
  is one subclass; paid alternatives (Polygon, Alpha Vantage,
  ORATS) plug in as additional subclasses with no schema, runner,
  comparator, or dashboard changes.
- **Cost reference**: Polygon ~$29/mo basic options tier;
  Alpha Vantage ~$50/mo; ORATS depends on volume. Documented in
  `tradeoffs.md` as the post-v1 swap path.

## 3. Black-Scholes IV computation when yfinance IV is missing / bad

- **What**: yfinance returns `impliedVolatility` but quality varies.
  Pricing module's `_iv_ok(iv)` predicate accepts `0.01 ≤ iv ≤ 5.0`
  finite values; anything else triggers re-computation via
  Brent's-method solver on the BS pricing function.
- **Mid-quote vs last**: prefer `0.5 * (bid + ask)` when both
  present and `ask >= bid > 0`; fall back to `lastPrice` otherwise.
- **Rate / dividend assumptions**: `r = 0.045` (rough US risk-free),
  `q = 0.0` (treat ETFs as zero-dividend for vol surface purposes).
  Stage 5 doesn't pull SOFR daily; the constant is good enough for
  vol-surface-of-vol-surface metrics. Documented in `tradeoffs.md`
  as a Stage 6+ refinement.

## 4. SVI fitting: spline fallback per prompt's explicit allowance

- **What**: We took the Stage 5 prompt's explicit fallback option:
  "cubic spline interpolation across log-moneyness per expiry
  slice, with the calendar arbitrage check". The full Gatheral SVI
  parameterization (5-parameter `w(k) = a + b*(rho*(k-m) + sqrt((k-m)² + sigma²))`)
  is documented in `tradeoffs.md` as the post-paid-data revisit.
- **Why fallback now**: Full SVI fitting needs reliable initial
  guesses (Quasi-Explicit SVI), constrained optimization, and
  deeper strikes than yfinance reliably provides for commodity
  ETFs. The spline fallback gives a smoothed surface + arbitrage
  check (the value-add of SVI for surface metrics) without
  hand-tuned optimization that might silently fail on bad chains.
- **Round-trip**: `SliceFit` dataclass stores knots + total-variance
  values; round-trips cleanly through JSONB in
  `options_surfaces.parameters`.
- **Calendar arbitrage check**: post-fit, samples total variance
  at k ∈ {-0.25, 0, 0.25} across slices ordered by expiry.
  Violations (later slice has lower variance than earlier at a
  sampled k) are surfaced in metadata; not currently auto-
  corrected — operator inspection is the resolution path.

## 5. BVAR analytical posterior over MCMC

- **What**: `signals/nowcasting/bvar.py` implements the univariate
  Bayesian linear regression with Normal-Inverse-Gamma conjugate
  posterior. Closed-form update formulas (~100 lines of numpy).
  No MCMC.
- **Why analytical over MCMC**: per the Stage 5 prompt's preferred
  approach. Conjugate analytical posterior is fast, deterministic,
  and well-documented (Banbura et al. 2010). MCMC would add
  pymc/numpyro dependency + sampling time per release — not worth
  it when the conjugate form gives identical posterior moments.
- **Minnesota-flavoured prior**: per-release univariate analogue of
  the standard VAR Minnesota prior. Own-lag prior mean = 1
  (random walk); lead-indicator prior means = 0; intercept
  effectively unrestricted via `intercept_tightness=100.0`.

## 6. Nowcasting design: per-release univariate regression, not full
VAR

- **What**: For each of 6 releases we fit a separate scalar
  regression on lagged target + lead indicators, not a joint VAR
  across all release series.
- **Why per-release**: releases have different frequencies (monthly
  / weekly / quarterly), different lead indicators, and different
  affected-instrument mappings. A joint VAR would conflate these
  cleanly-separable structures.
- **Surprise computation**: `(predicted_next - last_actual) /
  predictive_std` (BVAR uses the posterior predictive std; OLS
  uses 5% of `|last_actual|` as a heuristic denominator). Clipped
  to `[-3, 3]`, tanh-squashed. Sign convention: positive surprise
  is bullish for the release's affected instruments (gold/silver
  on hawkish CPI / dovish NFP, HG/ALI/CL on strong ISM, etc.).

## 7. Alt-data: no comparator (per prompt)

- **What**: All three alt-data signals are BASELINE under
  `alt_data_signal`. No comparator runs.
- **Why**: each method targets a different sub-universe (EIA
  covers crude/NG; USDA covers grains; Google Trends covers a
  scattered mix). "Method A vs method B" comparison isn't
  meaningful when the universes are disjoint.
- **Composite handling**: Stage 7 will combine all three via the
  standard `z * confidence` weighting; uncovered instruments
  receive `confidence=0` so they're ignored.

## 8. Heatmap extended to 10 columns; column-selection control
already handles it

- **What**: Added `vol_surface_signal`, `nowcasting_signal`,
  `alt_data_signal` to both `frontend/src/stores/signalsView.ts`'s
  `ALL_COMPONENTS` AND `api/routers/signals.py`'s
  `COMPONENTS_FOR_HEATMAP`. Stage 4C's checkbox control handles
  the table density automatically.
- **No new dashboard pages this stage**: the three new families
  rely on the heatmap + the existing per-component drill-in
  pattern. Dedicated vol-surface (with Plotly 3D), nowcasting,
  and alt-data pages are deferred to Stage 5B / 6 frontend pass.
  Documented in `tradeoffs.md`.

## 9. Tests written this session but not exercised against live DB

- The Stage 5 prompt asks for integration tests; the harness shell
  lost Postgres connectivity mid-session (alembic upgrade timed
  out). All Stage 5 tests are unit tests + pure-numpy math tests.
  Integration coverage for the three new families (full pipeline
  through `signal_values`) is queued as Stage 6 housekeeping in
  `next.md`.
