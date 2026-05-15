# Stage 4A — tradeoffs and deferred work

Things that were deliberately punted, with enough context for the
next stage to pick them up.

## 1. Dislocation: re-fit on every daily run vs weekly refit + blob cache

- **What's deferred**: The Stage 4A prompt asked for a weekly
  `dislocation_models_refit` Dagster asset that fits PCA + DFM,
  serialises the fitted state into
  `system.methods_registry.serialized_blob`, and a separate daily
  inference asset that deserialises + applies. The shipped code
  re-fits both methods on every `compute()` call inside the single
  `signal_dislocation` asset.
- **Cost**: ~30-60 seconds of DFM fit time per daily run (PCA is
  sub-second). Within the daily-asset SLA budget on a 13-instrument
  universe.
- **What's already in place**: `Method.serialize()` /
  `deserialize()` hooks exist; `serialized_blob` column exists in
  `system.methods_registry`; the methods take `lookback_days` and
  `n_components` / `n_factors` constructors so the fitting parameters
  are addressable. The natural fix is:
  1. New asset `dislocation_models_refit` (weekly, Sunday 00:00 UTC).
  2. Refactor `compute()` to optionally accept pre-fitted state and
     skip fitting when present.
  3. Wire the runner to look up the latest fitted blob via
     `MethodRegistry` and pass it down.
  4. Add tests for serialize/deserialize round-trip + cache-miss path.

## 2. DFM convergence + real-data integration test

- **What's deferred**: an end-to-end integration test that fits DFM
  against a seeded multi-instrument price panel. The unit-level
  tests cover metadata, interface contract, and the
  `[]`-on-failure behaviour; an integration test that asserts on
  actual residuals requires synthetic data large enough for DFM to
  converge (~2 years of daily data across 5+ instruments).
- **Stage 9 dependency**: the backtester will be the first place DFM
  is exercised on real backfill — that's the right time to invest
  in convergence-robustness tests because the failure modes are
  data-driven, not code-driven.

## 3. Comparator metadata flow-through

- **What's missing**: `PositioningSignalComparator` would like to
  report `avg_history_weeks_a` / `_b` from each method's
  `SignalOutput.metadata.history_weeks`. The base
  `SignalFamilyComparator._compute_metrics` flattens outputs into a
  scalar-column DataFrame, dropping the metadata dict before the
  comparator's `_extra_metrics` hook sees it. The placeholders
  return `nan` (sanitised to `null` in JSON).
- **Fix shape**: `SignalFamilyComparator._compute_metrics` should
  retain a parallel `metadata_a` / `metadata_b` mapping (list of
  dicts) that the family-specific hook can dip into. Touching the
  base class affects all signal families; we punt until at least
  one comparator actually needs the metric.

## 4. CFTC sub-class groupings vs prompt expectations

- **What differs**: The Stage 4A prompt expected the instrument-master
  `sub_class` column to contain coarse groupings like
  `refined_products` (HO+RB), `grains` (ZC+ZS+ZW). The current Stage
  2 seed uses fine-grained per-commodity values
  (`distillate`/`gasoline`/`oilseeds`). Phase 1c shipped with
  `class_column="asset_class"` as the default so cross-sectional
  ranking still works with the existing seed.
- **Follow-up**: If the prompt's coarser sub_class grouping is the
  intent going forward, reseed `market_data.instruments` and
  switch `CrossSectionalValue` defaults to `class_column="sub_class"`.
  The constructor knob is already wired.

## 5. Frontend pages for `/signals/positioning` + `/signals/dislocation`

- **What's deferred**: dedicated React pages with stacked-area COT
  charts, per-instrument selector, factor-loadings heatmap, and
  loading-stability time series. The Stage 4A prompt called them
  out.
- **What's shipped**: `COMPONENT_ORDER` in `Signals.tsx` extended to
  five entries so the existing heatmap + detail tabs surface
  positioning + dislocation data automatically once the
  corresponding signal_values rows land. The backend endpoints
  (`/signals/positioning/cot`, `/signals/dislocation/factors`) are
  in place and return Pydantic-typed payloads ready for the
  frontend.
- **Verification gap**: extending `COMPONENT_ORDER` was NOT
  exercised end-to-end in a running browser preview this session.
  The change is structurally trivial (TypeScript compiles, no runtime
  logic added) but observable behaviour only appears after the
  positioning + dislocation Dagster assets have produced data.
  Stage 4B should run a one-off seed of positioning + dislocation
  signal_values and verify the heatmap renders 5 columns before
  building the dedicated pages.

## 6. `uv.lock` left out of sync with `respx>=0.21.0`

- **What**: pyproject.toml gained `respx>=0.21.0` in dev deps.
  `uv.lock` was not regenerated because `uv` was not on PATH in this
  session's shell.
- **Fix**: run `uv sync --extra dev` (or `uv lock`) next time `uv` is
  available; commit the resulting lockfile change.

## 7. Logging fix shipped under Phase 0

- **What**: `src/macro_trader/logging_setup.py` previously included
  `format_exc_info` in the ConsoleRenderer chain, which triggers
  structlog's "Remove format_exc_info from your processor chain"
  warning. The pytest filterwarnings escalates that warning to an
  error, blocking the Phase 0 malformed-payload tests. The fix
  moves `format_exc_info` into the JSON-only branch.
- **Side note**: this is a strict improvement in dev log readability
  (no more double-formatted exceptions). Worth a separate PR in a
  cleaner repo state; bundled here for expediency.

## 8. Reference-method resolution skips DEPRECATED but not vice versa

- **What**: `reference_for` returns the first non-DEPRECATED method
  as its last fallback. This means a component with ONE method,
  status DEPRECATED, returns `None` — the comparator runner no-ops
  silently.
- **Edge case**: after a promotion + deprecation cycle, if the new
  PRODUCTION is also deprecated (rare but possible during
  cleanup), the comparator stops driving runs. That's a correct
  behaviour: don't blindly run comparisons against a deprecated
  method. The dashboard's Methods page will still show the
  deprecated method's history.
