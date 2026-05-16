# Stage 4C — decisions

## 1. Phase 1.0: column-selection state lives in Zustand, persisted to localStorage

- **What**: New store `frontend/src/stores/signalsView.ts` holds
  `visibleComponents: string[]` + `toggleComponent` /
  `showAll` / `resetToDefaults` actions. Persisted with Zustand's
  `persist` middleware under the localStorage key
  `macro-trader.signals-view`.
- **Why Zustand over URL params**: the column-selection state is a UI
  preference, not a deep-linkable view. Sharing a URL that hides
  half the columns just hides them for the reviewer too, which is
  rarely what we want. localStorage means each user's preferences
  follow them across sessions without polluting URLs.
- **`ALL_COMPONENTS`** is exported from the same module so the
  signals page and any future component-aware view share the same
  canonical list of seven entries (trend / carry / value /
  positioning / dislocation / factor_exposure / catalyst).
- **Default state**: all-checked. New components added in future
  stages won't be silently hidden — `resetToDefaults` re-includes
  everything from the current `ALL_COMPONENTS` list.

## 2. Phase 1.x: Recharts as the single chart library across pages

- **What**: every new page uses `recharts` (`AreaChart`,
  `LineChart`, `BarChart`) directly. The `lightweight-charts` and
  `plotly.js` dependencies are kept in `package.json` but not used
  by Stage 4C.
- **Why**: Stage 3's heatmap already established Recharts as the
  in-repo norm. Mixing chart libraries forces every reviewer to
  context-switch between APIs and triples the surface area for
  Vitest mocks. One library, one shape of test, one set of
  responsive-container quirks.
- **Where Recharts isn't enough** (e.g. factor heatmap with cell-
  level diverging-colour control): use a plain HTML table with
  Tailwind classes — same approach as the existing main heatmap.
  Avoids pulling in a fourth chart library.

## 3. Phase 1.x: empty-state UI patterns

- Every new page distinguishes three states:
  1. **Loading**: small "Loading..." text inside the relevant card.
  2. **Error**: small "Could not load <thing>." in
     `text-destructive` colour.
  3. **No data yet**: a dedicated Card with title + CardDescription
     explaining *why* there's no data and what to run (e.g.
     "Run the positioning ingest in Dagster first").
- **Why**: a blank panel with no explanation looks broken;
  explicit "run X to populate" prompts make the system's state
  legible to non-developer operators.
- **Special case**: the Causal Forest path on the factor exposure
  page detects the EconML-missing case explicitly and shows
  "Causal Forest unavailable" with install instructions, rather
  than the generic empty-state.

## 4. Phase 1.x: per-page detail flow is "click an item, see its
detail card"

- **Factor exposure page**: click an instrument row in the loadings
  table -> bottom card shows the per-factor contribution
  breakdown (`-loading × z_today`).
- **Catalyst page**: click an instrument bar in the pressure chart
  -> bottom card shows the historical event-by-event returns for
  that instrument.
- **Why this pattern**: keeps the heaviest API call (per-instrument
  detail) on-demand. The summary panels render fast; only when the
  user actually wants the detail do we hit the slower endpoint.

## 5. Phase 1.x: catalyst page surfaces the placeholder caveat

- **What**: the method selector label for `catalyst.causal.v1`
  reads "Causal (placeholder until Phase 2 lands)" so users
  understand why selecting it produces the same signal as the
  baseline. The methods page comparison row's
  `value_correlation_a_b = 1.0` is a corollary.
- **Why surface it on the page rather than in a tooltip**:
  the placeholder state is not a bug — it's a Stage 4B decision
  documented in `notes/stage_4b/decisions.md` §17. Calling it out
  in the dropdown prevents an operator wasting time investigating
  "why do both methods show the same number?"

## 6. Phase 1.x: routing under `/signals/<family>` not `/<family>`

- **What**: `/signals/positioning`, `/signals/dislocation`,
  `/signals/factor_exposure`, `/signals/catalyst`. The main
  `/signals` page gains a small nav row of links to each.
- **Why nest under `/signals/`**: groups the signals-related
  surface under one URL prefix; keeps the top-level URL space
  available for Stage 11's dashboard expansion (`/portfolio`,
  `/risk`, `/regime`, `/reports`).
- **Sidebar nav**: not added this stage — the existing site
  navigation pattern is "back to home / back to signals" buttons
  in each page's header. A persistent sidebar is Stage 11 work.

## 7. Phase 1.x: Vitest smoke tests, no Playwright

- **What**: one test file per page asserting that the header /
  selectors / empty-state render correctly when the API returns
  `[]`.
- **Why no Playwright**: full browser e2e tests add a CI install
  cost (Chromium ~150 MB) and a fragility surface (snapshot drift,
  timing flakes) that's not worth it for four pages that mostly
  display tabular data. Vitest + react-testing-library covers the
  important regressions (no crashes; selectors wired; loading and
  empty states correct).
- **Verification gap**: Vitest tests were written but NOT executed
  this session — pnpm / Node are not on PATH in the harness shell.
  TypeScript is structurally clean and tests mirror the existing
  `Methods.test.tsx` pattern; the user can run `pnpm test` to
  verify locally.

## 8. Phase 2.0: backfill fixture is opt-in via `@pytest.mark.real_data`

- **What**: `tests/integration/fixtures/backfill.py` exposes a
  session-scoped `backfill_panel` fixture that loads
  `tests/data/backfill_504d.parquet`. When the cache is missing
  the fixture calls `pytest.skip` instead of attempting to fetch
  fresh data on every test run.
- **Why opt-in**: yfinance + FRED on every CI run would be slow
  and flaky and would require API-key secrets in every fork.
  Local developers regenerate via
  `python -m tests.integration.fixtures.backfill` once they have
  `FRED_API_KEY` set.
- **Schema**: a single multi-sheet parquet with a `_dataset`
  column splitting (bars, factors, cot, calendar) into one
  DataFrame per consumer. One parquet load gives every
  consumer its slice.
- **Stage 5 reuse**: same fixture serves the future vol-surface +
  nowcasting validation tests; the schema is already wide enough.

## 9. Phase 2.1: factor_exposure.causal_forest.v1 — fix `est.fit()` to pass `X`

- **What**: Stage 4B's `CausalForestDML` call was
  `est.fit(Y=y, T=T, W=W)`. EconML 0.16 raises `ValueError("This
  estimator does not support X=None!")`. Stage 4C passes `X=W`
  (controls double as heterogeneity features) so we get a
  dataset-average CATE via `const_marginal_effect(W).mean()`.
- **Why X=W**: we want a single average CATE per (instrument,
  factor), not per-row heterogeneity. Reusing the controls as the
  heterogeneity surface and averaging gives the conventional
  "average treatment effect controlling for the other factors"
  reading.
- **Stage 4B silently swallowed the failure**: the
  `pragma: no cover - EconML failure` try/except returned an
  empty `_state` instead of crashing. Stage 4C's synthetic-data
  test would have caught the bug at Stage 4B if it had been
  written then. Adding the test was as important as fixing the
  call.

## 10. Phase 2.1: `n_estimators` must be divisible by EconML's `subforest_size=4`

- **What**: EconML's `CausalForestDML` requires `n_estimators` to
  be a multiple of `subforest_size` (default 4). Stage 4B's
  default of 200 is fine; the synthetic test originally used 30
  and crashed with a clear-but-cryptic error. Bumped to 32.

## 11. Phase 2.2: catalyst.causal.v1 — replace placeholder with real CATE

- **What**: Stage 4B's `CausalCatalyst.compute()` delegated to
  `EventStudyCatalyst` and tagged metadata with
  `placeholder_for_cate=True`. Stage 4C replaces this with real
  CausalForestDML CATE per (instrument, event_subject) pair.
- **Treatment design**: binary T (1 on event days, 0 on a
  stratified random sample of non-event days for the same
  instrument over the same lookback). Continuous-treatment
  (surprise = actual − consensus) would have more statistical
  power but the calendar ingest doesn't yet populate consensus
  reliably; binary is the honest choice.
- **W (controls / heterogeneity)**: macro factor z-scores at the
  event date, drawn from
  `signals.factor_exposure.factors.build_factor_panel`. Today's
  `W` is the last row of that panel; `const_marginal_effect(W_today)`
  yields the per-pair CATE at current regime.
- **`min_events_for_cate=15`** vs Stage 4B
  `min_events_for_estimate=5`: CausalForestDML is data-hungrier
  than mean(|return|). Pairs with 5-14 events fall back to the
  event-study sensitivity rather than producing noisy CATE; falls
  are tagged `fallback=True` in the per-pair state so the
  dashboard can show coverage gaps. Output metadata gains
  `fallback_pairs: int`; the Stage 4B `placeholder_for_cate=True`
  flag is gone.

## 12. Phase 2.3: Methods page — `ShadowDifferentiationBadge`

- **What**: each component card on `/methods` now renders a small
  badge showing the latest `value_correlation_a_b` from
  `/signals/comparisons?component=...`. Three states:
  - `>= 0.95`: muted "near identical" (shadow may not provide
    a distinct signal — investigate before promoting)
  - `0.5-0.95`: green "shadow differentiated" (the desired range)
  - `< 0.5`: red "diverged" (verify the methodology)
  - "no data" / "loading" / "error" pills for empty states.
- **Why per-component, not per-pair**: each component has at most
  one materially-different shadow (factor exposure has two but
  the OLS-vs-RF pair is the more interpretable comparison; the
  most-recent comparison row drives the badge regardless of which
  shadow it covers).
- **Tooltip text** explains the threshold rationale so an operator
  doesn't need to memorise it.

## 13. Phase 2: `uv.lock` now includes the `[ml]` extras

- **What**: ran `uv lock` to regenerate the lockfile with EconML
  and its transitive deps (numba, llvmlite, shap, sparse, +
  forced sklearn downgrade 1.8.0 → 1.6.1 for EconML compat).
- **CI implication**: default `uv sync --extra dev` install
  doesn't pull EconML; the EconML-gated tests skip. CI that wants
  the causal paths exercised must use `uv sync --extra dev --extra ml`.
