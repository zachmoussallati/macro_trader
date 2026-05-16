# Stage 4B — tradeoffs and deferred work

Things that were deliberately punted, with enough context to pick
them up in Stage 4C / 5.

## 1. Frontend pages `/signals/positioning`, `/signals/dislocation`, `/signals/factor_exposure`, `/signals/catalyst`

- **What's deferred**: Stage 4A's frontend extension stopped at
  extending `COMPONENT_ORDER` so the heatmap auto-includes the new
  families. Stage 4B's prompt called for dedicated pages for
  positioning, dislocation, factor exposure, and catalyst with
  charts (stacked-area COT, factor heatmap, sensitivity bars, etc.)
  — none shipped this stage.
- **What's shipped**: backend API endpoints are all there
  (`/signals/positioning/cot`, `/signals/dislocation/factors`,
  `/signals/factor_exposure/factors`, `/signals/factor_exposure/loadings`,
  `/signals/catalyst/events`). The dashboard heatmap auto-extends
  via `COMPONENTS_FOR_HEATMAP` in `api/routers/signals.py` once
  frontend `COMPONENT_ORDER` is brought to 7 entries.
- **What still needs doing in Stage 4C-frontend**:
  - Extend `frontend/src/pages/Signals.tsx` `COMPONENT_ORDER` to
    7 entries (`positioning_signal`, `dislocation_signal`,
    `factor_exposure_signal`, `catalyst_signal`).
  - Build `/signals/positioning` page (stacked-area managed-money
    long/short/net, sparklines for the two methods).
  - Build `/signals/dislocation` page (PCA + DFM loadings heatmap
    side-by-side, explained-variance time series with weekly step
    changes for PCA).
  - Build `/signals/factor_exposure` page (factor heatmap 13x6,
    method selector OLS/RF/CF, per-instrument factor contribution
    bar chart).
  - Build `/signals/catalyst` page (upcoming events table, per-
    (instrument, subject) historical sensitivity table, current
    catalyst pressure bar chart).
- **Verification gap**: extending `COMPONENT_ORDER` was NOT
  exercised in a running browser preview this stage either. Stage
  4C should run a one-off seed of all 7 signal families and
  verify the heatmap renders cleanly before building the dedicated
  pages.

## 2. Causal Forest factor exposure — fit shape works, real-data convergence untested

- **What's deferred**: `CausalForestFactorExposure.fit_on_panels`
  loops over each factor, treats it as the "treatment" and fits a
  CausalForestDML with the rest as controls. The shape is correct
  and the unit tests (skipped without EconML) cover metadata +
  constructor-raises-without-extra. Real-data convergence
  diagnostics — pseudo-R^2 calibration, CATE stability across
  bootstrap folds — are not exercised this stage.
- **Why**: EconML installation is gated, so CI without `[ml]`
  doesn't see the path; running the synthetic-data fit test
  reliably needs >504 days of data and converged DML splits, which
  pushes test runtime from sub-second to tens of seconds and
  introduces flakiness.
- **Stage 4C / 9 follow-up**: a backfill against real yfinance +
  FRED data is the right place to harden CausalForest. Add
  integration tests gated on EconML availability + Postgres reach
  + at least 504 days of seeded bars.

## 3. Causal Catalyst is a Stage-4B placeholder

- **What**: `CausalCatalyst.compute()` delegates to the event-study
  method, just re-tagging `method_id` to `catalyst.causal.v1` in
  the metadata blob. The methods page and registered status
  (SHADOW) are correct; the underlying signal is identical to the
  baseline.
- **Why ship the placeholder**: end-to-end wiring (registry, refit
  blob, runner, Dagster asset, comparator pair) is testable now.
  Adding real CausalForestDML CATE estimation needs the same
  real-data convergence work as the factor exposure variant.
- **Implication for the comparator**: until Stage 4C, the
  catalyst comparator's `value_correlation_a_b` will be 1.0 — the
  conventional indicator that the shadow hasn't differentiated
  yet. The dashboard should surface this so an operator doesn't
  mistake placeholder agreement for actual signal agreement.

## 4. OLS / RF factor exposure use inner-join + dropna(how=any)

- **What**: `_aligned_xy` does an inner-join of the returns +
  factor panels, then drops rows with any NaN. If a single
  instrument has many NaN rows in its return series, the fit
  window shrinks for *every* instrument.
- **Test pinning the behavior**:
  `test_ols_returns_no_state_when_inner_join_drops_too_many_rows`.
- **Stage 4C improvement**: per-instrument fitting where each fit
  uses only that instrument's non-NaN rows aligned with factors.
  Adds a per-instrument alignment loop (~15 lines per method).
  Worth it once a real instrument like ALI has sparse history.

## 5. Catalyst sensitivity sign defaults to zero (magnitude-only)

- **What**: forward score's *sign* is zero by default — magnitude
  carries pending catalyst risk but direction (will the next CPI
  surprise high or low?) is not predicted.
- **Stage 6 follow-up**: regime classifier produces directional
  priors per (event_type, regime). Multiplying the catalyst
  sensitivity by that prior gives a signed signal.

## 6. Comparator metadata flow-through still missing

- **What**: `loading_correlation` for factor exposure and
  `sensitivity_correlation` for catalyst are not computed because
  `SignalFamilyComparator._compute_metrics` flattens
  `SignalOutput.metadata` before the family hook sees it.
- **Carried over from Stage 4A**: `notes/stage_4a/tradeoffs.md` §3.
  Same fix shape: thread per-row metadata through the merged
  DataFrame and let `_extra_metrics` dip into it.

## 7. Geopolitical events skipped from the catalyst signal

- **What**: `DEFAULT_EVENT_KINDS = ("data_release", "central_bank",
  "supply_event")`. Geopolitical events (`kind="geopolitical"`)
  are excluded from both the historical sensitivity estimation
  and the upcoming-event scoring.
- **Why**: the geopolitical kind is sparse / manually entered /
  noisy; mixing it with structured data releases would overstate
  sensitivity for any (instrument, subject) pair that picked up a
  one-off war / nationalisation / etc.
- **Stage 5 / Stage 10 (Claude analysis layer)**: tagged
  geopolitical events become useful when the brief layer can
  contextualise them. Until then, they're noise in the
  sensitivity model.

## 8. Multi-region calendar coverage

- **What**: calendar coverage is heavily US-focused (FRED releases,
  USDA WASDE, CFTC schedule, FOMC). Brazilian / Argentine soy and
  Chinese metals events are sparse.
- **Effect on Stage 4B catalyst signal**: the (instrument, subject)
  matrix is dense for US-affecting commodities (CL, BZ, GC, SI,
  ZC, ZS, ZW) but sparse for non-US-driven ones (HG to a degree).
  Confidence per instrument reflects this via `n_subjects /
  5.0`-cap.

## 9. Three-way comparator runs both pairs as expected

- The Stage 4A `run_comparisons_for_component` runner produces
  `(ols, rf)` and `(ols, causal_forest)` pairs for the factor
  exposure family. With EconML missing, only `(ols, rf)` runs —
  the CF method isn't registered, so the runner finds one shadow
  instead of two. Both behaviours are correct; tests
  `test_run_comparisons_for_component_handles_multiple_shadows`
  (Stage 4A) covers the multi-shadow path.
