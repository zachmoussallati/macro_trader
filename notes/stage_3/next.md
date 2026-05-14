# Stage 3 → Stage 4 handoff

Stage 4 covers Signal Library Part 2: positioning, dislocation, catalyst,
macro factor exposures. Plus catching up on the ingester-coverage debt
we deferred from Stage 3.

## What Stage 4 should do first

### Ingester HTTP-mocked coverage (deferred from Stage 3)

Bring `macro_trader.data.ingestion.*` to ≥60% coverage using `respx`
for httpx-based ingesters (cftc, eia, usda, noaa) and monkeypatch /
fixture for the library-wrapped ones (fred via fredapi, yfinance,
google_trends via pytrends). Test the same 5 scenarios per ingester:

1. Happy path with realistic payload.
2. Empty response.
3. Rate-limit / 429.
4. Malformed payload.
5. Retry exhaustion → freshness row records failure.

Plan: a shared `tests/fixtures/http.py` with helpers for stubbing each
provider's response shapes.

## Stage 4 signal families (reminder)

1. **COT positioning** — derive signals from `positioning.cot_weekly`.
   Baseline: managed-money net positioning z-score. Shadow: legacy
   commercial extremes (the legacy report we just added in Stage 3).
2. **Macro factor exposure** — Baseline: rolling OLS of instrument
   returns on macro factor returns. Shadow: causal forest / random
   forest (per the framework spec).
3. **Cross-asset dislocation** — Baseline: PCA on a panel of instrument
   returns; signal = residual to top-K factor reconstruction. Shadow:
   Dynamic Factor Model.
4. **Catalyst sensitivity** — Baseline: event-study average impact.
   Shadow: causal-inference correction.

Each gets the same 6-file structure as Stage 3 (`<family>/methods.py`,
`comparator.py`, `runner.py`, `register.py`) and a Dagster asset.

## Conventions Stage 4 should follow (stable as of Stage 3)

### Signal output schema
`signals.signal_values` is the only table; do NOT add per-family tables.
Use `metadata` JSON for family-specific extras (e.g., factor weights,
event-window length).

### `SignalInput` / `SignalOutput`
Stable. Stage 4 may need additional input fields (e.g., macro factor
series IDs). Add to `SignalInput.extras` keyed by family name rather
than expanding the dataclass.

### Comparators
Subclass `SignalFamilyComparator`. Override `_extra_metrics(merged)` if
you need family-specific metrics; the base measures direction-agreement,
rank-correlation, etc.

### Point-in-time
Use `macro_trader.data.loaders` for daily bars and macro series. For
positioning data, write a similar loader in `data/loaders.py` (or a
sub-module) — the same observation_ts pattern applies.

### Calendar awareness
Stage 4 catalyst-sensitivity signals are the first real consumers of
`macro_trader.calendar.api`. Use `events_in_window` + `is_blackout` to
mask data appropriately.

## API additions for Stage 4

The current `DESIGNATED_PER_COMPONENT` map in `api/routers/signals.py`
covers `trend_signal`, `carry_signal`, `value_signal`. Extend it with:

- `positioning_signal`
- `factor_exposure_signal`
- `dislocation_signal`
- `catalyst_signal`

The heatmap automatically picks them up; the page UI already iterates
over `COMPONENT_ORDER` in `frontend/src/pages/Signals.tsx`, which Stage
4 should extend.

## Dashboard additions for Stage 4

- `/signals/heatmap` will grow from 3 to 7 columns. Verify the table
  fits at desktop widths.
- Add a "Cross-component composite" weighting scheme to the Home Top
  Signals card — currently sums z×confidence; may want to switch to
  normalised contribution by family.
- New page or extension: positioning detail (managed-money flows over
  time per instrument).

## Open questions to resolve early in Stage 4

1. **Causal forest library**: `econml.dml.CausalForestDML` or roll our
   own? Decision needed in `notes/stage_4/decisions.md`. EconML's API is
   stable but the dep is heavy.
2. **PCA fit cadence**: refit weekly or daily? Probably weekly (Sunday
   00:00 UTC) since the principal components shouldn't change much
   day-to-day, and we want stable factor exposures.
3. **Event windows**: how wide for catalyst sensitivity baseline? Spec
   suggests ±1 day; revisit after first events go through.

## What's stable Stage 4 can rely on

- Methods framework (Stage 1).
- Data layer (Stage 2) + loaders module (Stage 3).
- `SignalMethod` / `SignalInput` / `SignalOutput` / family comparator
  pattern (Stage 3).
- `register_all_methods` central registration (Stage 3 extension).
- Signal output schema + heatmap / decay endpoints + dashboard pages.

## What might still change

- The `DESIGNATED_PER_COMPONENT` map will likely need a status-based
  resolution (pick the PRODUCTION method if one exists, else BASELINE,
  else the first registered method) rather than the hardcoded mapping.
- Comparator runners currently hard-code which method pairs to compare.
  Stage 4 with multiple shadows per family may need a more general
  "compare every shadow against the production" loop.
- `signals.signal_values` may want a partial index on
  `(signal_id, instrument_id)` if heatmap queries get slow.

## Stage 4 commit checkpoint cadence

- After ingester coverage cleanup
- After positioning family
- After factor exposure family
- After dislocation family
- After catalyst sensitivity family
- After API + dashboard extensions
- After tests passing
- After notes complete
