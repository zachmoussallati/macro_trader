# Stage 3 — decisions

## 1. SMA period choices: 10/30, 20/60, 50/200

- **What**: three SMA crossover baselines spanning short-, medium-, and
  long-term horizons.
- **Why**: classical trend-following uses these triplets to cover roll
  speeds from ~2 weeks (short) to ~10 months (long). The 50/200 "golden
  cross" is the most popular long-term variant in commodity literature.
- **Consequence**: ensemble blend smooths over horizon-specific failures.
  Configurable via `config.signals.trend.sma_*`.

## 2. SMA squash via `tanh(diff * 10)` to map into [-1, 1]

- **What**: signal output = `tanh((SMA_fast - SMA_slow) * 10)`. Operates on
  log-prices so the difference is unitless.
- **Why**: cross-method comparability needs a bounded range. tanh is
  smooth (unlike clip) and the *10 multiplier makes 0.1 log-units
  produce ~0.76, i.e., a moderately strong signal at typical crossover
  magnitudes.
- **Alternative considered**: z-score the SMA difference. Heavier code
  for similar effect.

## 3. Ensemble = equal-weighted; `regime_state` wired but ignored

- **What**: `TrendEnsemble` calls each component, then averages outputs
  via `signals.ensemble.equal_weighted`. The combiner accepts a
  `regime_state` arg that is unused in Stage 3.
- **Why**: Stage 6 will plug a regime classifier in. Wiring the interface
  now keeps Stage 6 from needing to refactor every ensemble method.
- **Consequence**: changing ensemble weights pre-Stage 6 is a one-liner
  in `ensemble.equal_weighted(weights=...)`.

## 4. HP filter lambda = 1600

- **What**: standard daily-data Hodrick-Prescott smoothing parameter.
  Documented in the method's metadata references.
- **Why**: 1600 is the canonical choice for quarterly data; for daily we
  often go higher (~129600), but the trend layer here is paired with a
  60-day rolling normalisation that absorbs some smoothing — so 1600
  produces a reasonable cycle component. We expose the parameter on the
  method, so re-tuning is a one-keyword change.
- **References**: Hodrick & Prescott (1997).

## 5. Carry placeholder: Option D from the prompt

- **What**: `CarrySpotProxy` runs daily, fills `signal_values` with
  near-zero outputs and `confidence=0` for most instruments.
  CL/BZ/GC/NG get a small drift-derived proxy with `confidence=0.3`.
- **Why**: building the framework against a real method end-to-end
  surfaces shape problems early. Composite scoring (Stage 7) weights by
  confidence, so low-confidence carry doesn't pollute decisions.
- **Consequence**: when Stage 12 ships futures-curve data, the carry
  enhancement slots in as a `SHADOW` method with no schema changes.

## 6. Value: rolling z-score (baseline) vs sub-class rank (shadow)

- **What**: baseline z-scores over a 252-day window; shadow ranks the
  same z-score within asset sub-class (energy / base / precious / ag).
- **Why**: cross-sectional value within a sub-class removes regime-wide
  drift (e.g., "all energies are up 30%" doesn't mean every energy is
  expensive — relative to peers, some are cheap).
- **Consequence**: shadow's `metadata.sub_class_ranked = True` lets
  downstream code distinguish the two ranking schemes.

## 7. Inverted sign for value: positive = cheap

- **What**: `tanh(-z.clip(-3, 3))`. Negative log-prices vs mean → positive
  signal (mean-revert long).
- **Why**: convention across trend / value / carry signals: positive
  raw_value = long bias. Makes composite scoring unambiguous and the
  dashboard heatmap interpretable at a glance.

## 8. Signal output shape: raw_value + zscore + rank + confidence

- **What**: every signal returns all four. Plus optional
  `rolling_sharpe_252` and JSON metadata.
- **Why**: Stage 7 composite scoring reads z-score + rank weighted by
  confidence. Raw value is for the dashboard and debugging. Stage 9
  walk-forward Sharpe will replace the in-sample rolling Sharpe.
- **Consequence**: building the schema once means no retrofit when new
  signal families land.

## 9. Z-score lookback: 252 trading days (one year)

- **What**: rolling-z lookback fixed in both `value.zscore.v1` and in the
  per-method standardisation step.
- **Why**: 252 is the standard "annualised" window in equity / commodity
  research. Shorter is too noisy at daily frequency; longer needs years
  of history before signals start.

## 10. Rolling Sharpe window = 252; vol target = 10% annualised

- **What**: `rolling_sharpe_252` in the SignalValue is computed against
  a 10%-annualised position size, lagged by 1 day for honesty.
- **Why**: vol-targeting normalises Sharpe across instruments with very
  different realised vol. 10% is a conservative target — not realistic
  for paper trading (way too small), but it produces a stable Sharpe
  signature.
- **Consequence**: Stage 9's walk-forward backtester writes a separate
  `rolling_sharpe_oos_252` to a new column when it lands; the in-sample
  number stays as a quick-look indicator.

## 11. Method-registration ordering

- **What**: `methods/setup.py:register_all_methods` calls `data_quality`
  first, then `trend_signal`, `carry_signal`, `value_signal`.
- **Why**: data-quality methods don't depend on signals. Signals
  register independently. Order is alphabetic-by-family for ease of
  reading; the registry itself doesn't care about order beyond
  PRODUCTION uniqueness per component.

## 12. Quality runner `_load_close_series` stays direct (no loader)

- **What**: the legacy data-quality flag pipeline keeps its own DB
  query, NOT the new calendar-aware `data.loaders.load_close_series`.
- **Why**: data quality flags align with *real* observation dates,
  including non-trading days when an ingester emits them. The new public
  loader reindexes to a market calendar, which masks weekend rows. Different
  use cases, different conventions.
- **Consequence**: future quality methods that want calendar-aware data
  can call the loader explicitly; the default stays direct.

## 13. Designated production trend signal: `trend.ensemble.v1`

- **What**: all four trend methods (3 SMAs + ensemble) are BASELINE per
  the spec. The registry's `production_for("trend_signal")` would return
  any one of them (no defined tiebreaker among BASELINEs).
- **Why**: Stage 7 composite scoring will reach for the ensemble by
  method_id directly (`get_method("trend.ensemble.v1")`), not via
  `production_for`. The component-wide query is a debugging convenience
  for the dashboard.
- **Consequence**: documented here + in the methods_registry note for
  Stage 7's consumers.

## 14. `signal_values.metadata` column collision

- **What**: SQLAlchemy `Base.metadata` is the MetaData object. Trying to
  name the column literally `metadata` and use `pg_insert(Model).values(
  {"metadata": ...})` confuses SQLAlchemy. Fix: route the insert through
  `SignalValue.__table__` (Core-level) and the Python attribute name
  `signal_metadata` is reserved for the ORM.
- **Why**: the schema spec used `metadata JSONB`; renaming the column
  would diverge from the prompt and the dashboard.
- **Consequence**: future models with a `metadata` column should follow
  the same pattern (Python attr `<thing>_metadata`, column `metadata`,
  insert via `__table__`).

## 15. `compute_all_signals_job` schedule = 23:30 UTC

- **What**: signals run after the 23:00 UTC `data_quality_job`.
- **Why**: signals need that day's quality flags to mask anomalies
  appropriately (and we run quality on yesterday's bars).
- **Consequence**: 30-minute buffer; will tighten if quality slows.

## 16. HP filter dependency-free implementation

- **What**: in-module dense linear solve (`np.linalg.solve`) instead of
  `statsmodels.tsa.filters.hpfilter`.
- **Why**: for 400-day windows the dense solver is fine and we avoid a
  ~50ms statsmodels import on every signal run. The sparse statsmodels
  version is faster only at >10000 points.
- **Consequence**: trade-off documented if anyone wants HP on very long
  series.

<!-- Continued as Stage 3 work progresses. -->
