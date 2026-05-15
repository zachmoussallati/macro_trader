# Stage 4A → Stage 4B handoff

Stage 4B picks up:

1. **Macro factor exposure** signal family — OLS baseline + RF /
   Causal Forest shadows. EconML integration is the first
   dependency-add of the stage; pin it as
   `econml>=0.15.0` in dev OR a new `[ml]` optional group depending
   on how heavy the install is.
2. **Catalyst sensitivity** signal family — event-study baseline +
   causal-inference enhancement. First real consumer of
   `macro_trader.calendar.api.events_in_window` and `is_blackout`.
3. **Frontend pages** for `/signals/positioning` and
   `/signals/dislocation` (deferred from Stage 4A; see
   `tradeoffs.md` §5).
4. **Dislocation weekly-refit asset** with
   `system.methods_registry.serialized_blob` cache (deferred from
   Stage 4A; see `tradeoffs.md` §1).

## What Stage 4B can rely on (stable as of Stage 4A)

- **`run_comparisons_for_component(component, comparator, data, *,
  period_start, period_end, session, notes)`**. Macro factor
  exposure has TWO shadows (RF + Causal Forest) — the runner
  already handles N shadows per component with zero changes.
- **`signals.designated.resolve_id(component)`** — the YAML override
  map in `config/base.yaml` is the place to pin a new component's
  designated method.
- **`COMPONENTS_FOR_HEATMAP`** in `api/routers/signals.py` — extend
  with `factor_exposure_signal` / `catalyst_signal` when ready;
  the heatmap query auto-picks them up.
- **`data/instruments.get_class_groups(session, instrument_ids, *,
  column=...)`** — use `"asset_class"` for cross-sectional
  groupings; `"sub_class"` once the seed is updated (see
  `tradeoffs.md` §4).
- **`load_cot_as_of`**, **`load_close_panel`**, **`load_macro_series`**
  in `data/loaders.py` — all enforce point-in-time correctness via
  `as_of`. Use them; do not query the underlying tables directly.

## Open questions for Stage 4B's `decisions.md`

1. **EconML vs roll-your-own causal forest**: EconML's
   `CausalForestDML` is a clean API but the package pulls in
   ~200MB of dependencies. The alternative is `sklearn`'s
   `RandomForestRegressor` plus a hand-rolled doubly-robust scoring
   step. EconML is the safer choice for replication but Stage 4B
   should benchmark both fits on the 13-instrument universe and
   document the call.

2. **PCA fit cadence**: Stage 4A defaulted to "re-fit on every daily
   run" for both PCA and DFM. The weekly refit asset (deferred)
   should refit on Sunday 00:00 UTC and persist the fitted state.
   Decision point: should the daily inference asset use the
   most-recent fit even mid-week, or re-fit on its own data if the
   weekly refit failed? Probably the former — Stage 4B should
   pick one and document.

3. **Event windows for catalyst sensitivity**: Stage 4A's prompt
   suggests +/-1 day around an event. Verify against the calendar
   coverage: FOMC events have multi-day fade; commodity-specific
   events (WASDE, EIA storage) are tighter. Per-event-type window
   config probably belongs in `config/base.yaml`
   `signals.catalyst.event_windows`.

## API + dashboard work for Stage 4B

The heatmap goes from 5 columns to 7. Check the desktop width fits
without horizontal scroll; if not, the existing checkbox column
selector in `Signals.tsx` is the right place to add filtering.

New API endpoints expected:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/signals/factor_exposure/loadings?as_of=&instrument=` | Current macro factor loadings for the chosen method (OLS / RF / CF). |
| GET | `/api/v1/signals/catalyst/events?as_of=&days_ahead=` | Upcoming events with computed sensitivity scores. |

Both follow the same pattern as `/signals/dislocation/factors`:
read latest snapshot from `signal_values`, attach method-specific
metadata.

## Tests Stage 4B should add up-front

- Macro factor exposure: synthetic 3-factor data with known
  exposures; both OLS and the shadow should recover the loadings to
  within reasonable tolerance.
- Catalyst sensitivity: seeded calendar events around a price
  panel with engineered jumps at event times; verify the signal
  flags the jump.
- Comparator: three-method comparison (OLS baseline, RF shadow,
  CF shadow) producing two ComparisonResult rows per run.

## What's still drifting / worth watching

- **DFM convergence robustness**: untested against real data this
  stage. Stage 4B should run the dislocation pipeline against a
  yfinance backfill and watch for the `dfm.fit_failed` log line.
- **`signal_values.metadata`** JSONB growing in size: positioning
  rows now carry `report_type` / `is_extreme` / `is_fresh_data` /
  `history_weeks` / `publication_ts`. If the index on
  `(signal_id, instrument_id, value_ts, observation_ts)` starts
  bloating, consider promoting hot metadata fields to columns.
- **`uv.lock`** is out of sync with `respx>=0.21.0` from Stage 4A.
  Run `uv sync --extra dev` in a shell with `uv` available and
  commit the resulting lockfile change.

## Stage 4B commit checkpoint cadence

- After macro factor exposure family
- After catalyst sensitivity family
- After API + dashboard extensions (positioning / dislocation pages
  + new factor / catalyst endpoints)
- After dislocation weekly-refit asset
- After tests + notes
