# Stage 4C — tradeoffs and deferred work

What was punted, with enough context to pick up in Stage 4D / 5.

## 1. Frontend tests written but NOT executed this session

- **What**: 4 new Vitest smoke tests under `frontend/src/test/`
  mirror the existing `Methods.test.tsx` pattern (mock `fetch`,
  assert header + selectors render, assert empty-state UI shows
  when API returns `[]`).
- **Why not run**: Node / pnpm aren't on PATH in the harness shell
  used this session. The TypeScript + React code is structurally
  clean (matches existing patterns; same imports as
  `Methods.test.tsx`).
- **Verification path**: user / CI runs `pnpm install && pnpm test`
  to validate. Any breakage will show up there.

## 2. Real-data backfill cache not generated

- **What**: `tests/integration/fixtures/backfill.py` exists with
  the regenerate CLI (`python -m tests.integration.fixtures.backfill`)
  but the cache file `tests/data/backfill_504d.parquet` was not
  created. Every `@pytest.mark.real_data` test currently skips
  with "backfill cache not found".
- **Why**: regeneration requires `FRED_API_KEY` (and ideally
  internet access for yfinance). Both were not configured for the
  harness shell that ran Stage 4C.
- **What's shipped**: full infra. Once a developer with API keys
  runs the regenerate CLI once, the cache lands in
  `tests/data/backfill_504d.parquet` and every `real_data` test
  starts running on subsequent CI / local invocations.

## 3. CATE validation tests are synthetic, not real-data

- **What**: the new factor-exposure CF synthetic test
  (`test_cf_fits_on_synthetic_panel_and_emits_state`) verifies the
  fit converges and produces finite CATE values. The Stage 4C
  prompt's stricter validation contract — "CATEs differ from
  baseline for ≥30% of pairs", "value_correlation between
  event_study and causal between 0.4-0.8" — needs the backfill
  cache.
- **Why**: synthetic data with engineered linear factor structure
  doesn't materially differentiate OLS, RF, and CF — the CATE
  from CF will roughly equal the OLS β. The interesting case
  (CATE materially different from β) requires real data with
  non-linear / regime-dependent structure.
- **Stage 4D / 9 pickup**: with the backfill cache, write
  `tests/integration/signals/test_factor_exposure_cf_real.py`
  and `test_catalyst_causal_real.py` exercising the validation
  contract. Both gated on `@pytest.mark.real_data`.

## 4. CATE refit timing not measured at scale

- **What**: the Stage 4C prompt asked for "Documented observed
  timing for CF refit" (per Phase 2.1). Synthetic-test fits run
  in <2 seconds, which isn't representative of the 13-instrument
  × 6-factor weekly refit on 504 days (estimated 5-15 minutes per
  the spec).
- **Stage 4D / 9 pickup**: regenerate the backfill cache, run
  `python -m macro_trader.signals.factor_exposure.refit` end-to-end,
  log wall-clock time, and update `notes/stage_4c/comparison_results.md`
  with the actual numbers.

## 5. Catalyst CATE: binary T not surprise-magnitude T

- **What**: Stage 4C uses binary treatment (event occurred = 1,
  randomly-sampled non-event day = 0). Continuous treatment
  (surprise = actual − consensus) has more statistical power but
  requires the calendar ingest to populate consensus values
  reliably.
- **Calendar audit**: a quick `select kind, importance,
  jsonb_typeof(metadata->'consensus') from calendar_events` would
  tell us coverage. Stage 4D should run this audit before
  switching the treatment shape.
- **Switch shape**: change `discrete_treatment=True` → `False` in
  the `CausalForestDML` constructor inside
  `_fit_cates_per_pair`. The rest of the code is treatment-shape
  agnostic.

## 6. Frontend column-selection state is local-only

- **What**: the Zustand store persists to `localStorage` so
  preferences survive reloads, but a user with multiple devices
  has different state on each. Server-side persistence would
  require an `auth.user_preferences` table + API surface.
- **Why deferred**: not worth the schema for what is plainly a
  per-browser preference. Revisit if multi-device usage becomes
  common.

## 7. No Playwright e2e tests

- Same reason as Stage 4B: Vitest + react-testing-library covers
  the important regressions; Chromium installs add CI cost we
  can't justify for four pages of mostly-tabular display.

## 8. Methods page differentiation badge — single threshold per
component

- **What**: `ShadowDifferentiationBadge` reads only the
  most-recent comparison row per component, so when a component
  has multiple shadows (factor_exposure: RF + CF) only one pair
  drives the badge color. Whichever pair was compared last wins.
- **Stage 4D / 5 fix**: render one badge per (baseline, shadow)
  pair when there are multiple. Adds two columns of layout for
  factor_exposure + future families with multi-shadow setups.

## 9. The chart-library pick (Recharts only) leaves Plotly +
lightweight-charts unused

- **What**: `package.json` still pulls in `plotly.js-dist-min` and
  `lightweight-charts` from earlier stages. They're not used by
  any Stage 4C pages.
- **When to remove**: Stage 11's dashboard polish should audit
  unused deps. For now leaving them in place avoids breaking
  any future page that wants candlestick charts (which Recharts
  doesn't do well).
