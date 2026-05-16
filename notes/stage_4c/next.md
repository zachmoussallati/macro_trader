# Stage 4C → Stage 5 handoff

Stage 5's prompt covers vol-surface, nowcasting (BVAR), and
alt-data signals. Three Stage 4C deliverables to verify before
Stage 5 begins:

1. **`pnpm test`** locally to actually execute the four new Vitest
   files. Stage 4C wrote them but couldn't run them in the harness
   shell. Any breakage is mechanical (missing import, wrong
   selector matcher).
2. **Generate the backfill cache** (`python -m
   tests.integration.fixtures.backfill`) and run `pytest -m
   real_data`. Today every real_data test skips with "cache not
   found" — running once per environment is the gating check.
3. **Browser-verify the new pages** against a live API. The 7-
   column heatmap, the per-component pages, and the Methods page
   differentiation badge are all observable in the browser only;
   no Vitest test asserts on full rendering.

## What Stage 5 can rely on (stable as of Stage 4C)

- All seven signal families have backend + frontend coverage:
  registered methods, refit infra, daily runner, Dagster wiring,
  per-component dedicated page, comparator picture.
- `[ml]` extras pattern is proven — Stage 5's nowcasting if it
  needs causal modelling can reuse the same gate.
- Backfill fixture infrastructure is reusable for any Stage 5+
  validation tests; just add the new data type to
  `Backfill` dataclass + `regenerate()` + the parquet schema.
- Methods page differentiation badge generalises to any new
  components — Stage 5 vol-surface / nowcasting / alt-data
  components automatically get a badge once they register their
  first comparator run.

## What Stage 5 + Stage 4D should add

### Stage 4D housekeeping (small)

- Generate + commit the backfill cache (or document the
  regenerate command in CI runbook).
- Add `tests/integration/signals/test_factor_exposure_cf_real.py`
  + `test_catalyst_causal_real.py` exercising the Stage 4C
  validation contract:
  - CATEs differ from baseline for ≥30% of pairs
  - `value_correlation` between event_study and causal in
    [0.4, 0.8]
  - serialization round-trip preserves CATE estimates exactly
- Document observed CF refit timing (Stage 4C prompt's
  Phase 2.1 "Watch for: 5-15 min per weekly refit").
- Per-pair differentiation badges on the Methods page (currently
  one badge per component — Stage 4D extends to one per
  shadow).

### Stage 5 — vol surface + nowcasting + alt-data

- **Vol surface signal family**: SVI parameterisation. Free
  commodity options data is scarce; pin the source choice early
  (CME end-of-day vs paid feed).
- **Nowcasting signal family**: simple OLS-AR baseline + BVAR
  shadow on US macro indicators. Will be the third real consumer
  of the calendar API (after catalyst + future Stage 6 regime
  classifier).
- **Alt-data signals**: Google Trends-derived sentiment, EIA
  weekly storage surprises, USDA WASDE deviations from consensus.
  Already-ingested in Stage 2; Stage 5 turns the data into
  signals.

## Open questions for Stage 5's `decisions.md`

1. **Vol surface data source**: CME end-of-day vs paid academic
   feed. Pin the choice early — affects what kind of vol-surface
   signals are even feasible.
2. **BVAR prior**: literature standard is Minnesota; verify it
   makes sense on the small US-macro indicator set we have.
3. **Alt-data confidence**: Google Trends is noisy; how much
   smoothing before the signal is usable?

## API + dashboard work for Stage 5

The heatmap goes from 7 columns to ~10. The Stage 4C checkbox
selector handles the layout; Stage 5 just needs to add the new
component keys to `ALL_COMPONENTS` in
`frontend/src/stores/signalsView.ts`.

New API endpoints expected (per Stage 5 prompt):

| Path | Purpose |
| --- | --- |
| `/signals/vol_surface/slices?as_of=&instrument=` | Latest IV slice + SVI fit |
| `/signals/nowcasting/projections?as_of=` | Current nowcast + uncertainty |
| `/signals/alt_data/sentiment?as_of=` | Latest alt-data signal aggregates |

## What's drifting / worth watching

- **`signal_values.metadata`** JSONB row size grows again with
  CATE state stored per row in factor_exposure / catalyst rows.
  Watch query times.
- **EconML version pinning**: `econml>=0.15.0` could pull a
  breaking 1.0 release. Pin `<0.20` or similar before that
  happens.
- **sklearn pin**: EconML forced sklearn down to 1.6.1. Stage 5
  / 6 need to verify nothing else needs >1.6.

## Stage 5 commit checkpoint cadence

- After vol-surface family
- After nowcasting family
- After alt-data signals
- After API + dashboard extensions (vol-surface + nowcasting
  + alt-data pages)
- After tests + notes
