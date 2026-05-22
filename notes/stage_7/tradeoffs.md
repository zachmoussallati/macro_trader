# Stage 7 — Tradeoffs

What was deferred, why, and what it would take to revisit.

## Deferred this stage

### 1. Per-instrument GBM models

`composite.gbm.v1` trains one model for all 13 instruments and
relies on instrument one-hot features to differentiate. The
per-instrument alternative would be 13 separate models. We chose
the global model because:

- 4-year quarterly retrains at ~3 million row-features each is
  already non-trivial; 13× that fights the quarterly budget.
- Instrument one-hot features give LightGBM enough cross-instrument
  separation in practice for commodities; the per-instrument lift
  is usually <5% on Sharpe per Friedman 2001 §10.
- Per-instrument loses cross-instrument regularisation (each model
  only sees its own data).

To revisit: bench global vs per-instrument on the first 18 months
of live composite history; if global lags per-instrument-mean by
>15% on top_5_overlap, switch.

### 2. Smooth posterior averaging in `composite.bayesian_hier.v1`

The Bayesian method currently uses the MAP weight estimate per
(regime, instrument) cell. Full posterior averaging (drawing N
weight samples and averaging the resulting scores) would surface
the posterior uncertainty directly into the composite score.

We chose MAP because:

- The NIG conjugate posterior is closed-form, so we already have
  the analytical mean and variance available in
  `BayesianRegressionResult` (Stage 5 BVAR pattern). Posterior
  averaging is a refactor away.
- 13 instruments × 5 regimes × 10 signal methods = 650 cells; per
  cell sampling 100 weight draws is ~65k extra multiplies per
  daily compute. Manageable but unneeded until we have a use case
  for the resulting posterior width.

To revisit: when Stage 9's backtester wants posterior-width-based
position sizing, plumb the credible interval through to the
`composite_metadata` JSONB.

### 3. Multivariate MS-VAR (still deferred from Stage 6)

`regime.msvar.v1` uses statsmodels MarkovRegression on PC1 of the
feature panel (univariate fallback). The full multivariate version
needs a hand-rolled EM or a paid library and was deferred. Stage 7
composite scoring works fine on the univariate output via
probability-weighted blending, so this doesn't bottleneck us yet.

### 4. Confidence calibration of the GBM composite

LightGBM regression outputs aren't probability-calibrated. The
GBM composite reports its prediction as both `raw_score` (the
model output) and `score` (tanh-squashed). Calibration (e.g.
isotonic regression on a held-out fold mapping raw_score → posterior
probability of positive next-day return) would let downstream
position sizers in Stage 8 size by probability rather than by raw
magnitude.

Skipped because: Stage 8 hasn't been built yet, and the linear +
Bayesian methods both have explicit confidence semantics already.
GBM's `score` is comparable to the others in sign and magnitude;
the missing piece is the credible interval.

### 5. Dynamic weight adjustment between attribution refreshes

Weight snapshots refresh weekly. If a regime persists for 6 weeks
and the composite is steadily underperforming, the weights only
react at the next Sunday attribution refresh. A more aggressive
system would dynamically adjust weights based on realised vs
expected performance day-by-day.

Skipped because: the EWM smoothing already dampens week-to-week
swings, and dynamic per-day adjustments without proper
performance attribution risk overfit. Stage 9's walk-forward
backtester is the right place to evaluate whether faster
adaptation is worth the noise.

### 6. Per-instrument transition multipliers

`transition_multiplier()` returns one scalar per `as_of` based on
the BOCPD changepoint probability of the regime feature panel as a
whole. Some instruments are more sensitive to regime transitions
than others (e.g. natural gas vs gold during a vol_spike). Per-
instrument multipliers would condition on instrument-specific
volatility or beta.

Skipped because: the global multiplier is a sound first pass and
Stage 8 can layer per-instrument risk adjustments on top of the
composite score.

### 7. Real-time Vitest CI

The 9 Vitest test files (Methods + 7 signal pages + Regime +
Composite) exist and follow the established mock-fetch +
MemoryRouter pattern. They've never actually run in this harness
because Node is not on PATH. A CI lane that ships a Node toolchain
would close this gap permanently.

Documented as a Stage 8 follow-up in `next.md`.

### 8. Backfill cache for real_data tests

`tests/integration/fixtures/backfill.py:regenerate()` requires
`FRED_API_KEY` and pulls live yfinance + FRED data. The harness
session doesn't have either. The 2 real_data tests skip cleanly
when the cache is absent; a one-time local regen unblocks them.

Documented in `decisions.md` Phase 0.2.

## Deliberate non-features

These weren't deferred — we explicitly decided against them.

### Hard-switching weights per regime

Considered: `weights = weights_table[argmax(probability_vector)]`.
Rejected because regime labels can flip on a 2% probability swing
and hard-switching introduces a 50% step in the weights. Probability-
weighted blending uses all available information and is smooth.

### Negative-Sharpe weight inversion

Considered: `raw_weight = sharpe` (allow negative). Rejected
because a signal with negative historical Sharpe in a regime
worked *backwards* there — but the historical relationship is
fragile and inversion compounds when it reverts. Zero weight is
the safer position.

### Composite as 11th heatmap column

Considered (per Stage 6's `next.md`). Rejected because composite
is a different shape (1 value per instrument) from signal families
(10 signal × instrument matrix). Composite is shown in `/composite`
as a ranked instrument table, not as an 11th column on `/signals`.

### Confidence multiplier on the final composite score

Considered: scale the final `score` by `confidence`. Rejected
because we already multiply per-signal contributions by per-row
confidence inside `raw_score = sum(weight * z * confidence)`.
Double-scaling at the score level would over-attenuate.

## Open questions for Stage 8

- Composite scores are bounded in `[-1, 1]` after tanh. Stage 8's
  position sizer needs a sign-preserving transform from
  `score → position weight`. Linear scaling × portfolio risk
  budget is the obvious first cut; ERC / HRP / BL / CVaR are the
  Stage 8 menu.
- The `confidence` field on `composite_scores` is the median of
  contributing per-signal confidences. Stage 8 might want a richer
  signal — e.g. the std of the per-signal contributions as a
  signal-disagreement measure. That data is in `composite_metadata`
  already; Stage 8 only needs to surface it.
- Drawdown gates (-5% / -8% / -10%) become live in Stage 8. Likely
  these dampen the composite at the portfolio level rather than at
  the score level (so the score itself stays a pure directional
  view).
