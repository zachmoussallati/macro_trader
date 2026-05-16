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

<!-- Phase 2 decisions appended after the CATE work lands. -->
