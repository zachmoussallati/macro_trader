# Stage 4B → Stage 5 (and Stage 4C frontend) handoff

Stage 5's prompt covers vol-surface, nowcasting (BVAR), and alt-data
signals. Before getting there, two things should land:

1. **Stage 4C frontend** (deferred from Stage 4B Phase 0.4):
   dedicated `/signals/positioning`, `/signals/dislocation`,
   `/signals/factor_exposure`, `/signals/catalyst` pages.
2. **Stage 4C harden-the-shadows**: real-data CATE for
   `factor_exposure.causal_forest.v1` and `catalyst.causal.v1`
   (currently a placeholder).

## What Stage 5 can rely on (stable as of Stage 4B)

- All seven signal families implement `compute()` returning the
  standard `list[SignalOutput]` schema, with cross-sectional ranks
  and confidence per row.
- Three families (dislocation / factor_exposure / catalyst) cache
  fitted state via `system.methods_registry.serialized_blob` and
  load it on daily inference. The pattern (refit module + Dagster
  asset + load_*_state in runner) is replicable for any new family
  with non-trivial fitting cost.
- `signals.factor_exposure.factors.build_factor_panel` and
  `latest_factor_zscores` give Stage 5's nowcasting + vol-surface
  signals access to the same six macro factors with identical
  transforms / z-score windows.
- `signals.catalyst.events.upcoming_score` is reusable by any
  signal that wants forward-event weighting (vol-surface signals
  could weight implied vol by upcoming-event sensitivity, for
  example).
- `Method.serialize()` / `deserialize(blob)` + the `b"ZLIB"` magic
  prefix compression pattern in each refit module.

## What Stage 5 + Stage 4C should add

### Stage 4C frontend (separate from Stage 5)

- Extend `frontend/src/pages/Signals.tsx` `COMPONENT_ORDER` to 7
  entries.
- Build dedicated pages per the Stage 4B prompt's spec
  (positioning stacked-area COT + sparklines, dislocation factor
  loadings + explained variance, factor exposure factor heatmap +
  per-instrument contributions, catalyst upcoming events table +
  catalyst pressure bar chart).
- Run a one-off seed of all 7 signal families before building the
  pages so the heatmap rendering can be verified end-to-end in
  the browser.

### Stage 4C harden-the-shadows

- Real CausalForestDML CATE for `catalyst.causal.v1`. The
  placeholder structure is in place; ~1 day of work to wire real
  CATE estimation into `_inner.fit_on_session`.
- Real-data integration tests for the three EconML-gated paths
  (factor_exposure CF + catalyst causal). Gate on `EconML +
  Postgres + at least 504 days of seeded bars`.

### Stage 5 — vol surface + nowcasting + alt-data

- **Vol surface signal family**: SVI parameterisation (baseline:
  raw IV slice; shadow: SVI fit). Free commodity options data is
  scarce; Stage 5 will document where to source it (CME's
  end-of-day datasets are paywalled; QuantConnect / Polygon options
  free tiers exist for equities but commodities require care).
- **Nowcasting signal family**: simple OLS-AR baseline + BVAR
  shadow on US macro indicators. Will be the second consumer of
  `macro_trader.calendar.api` (after catalyst).
- **Alt-data signals**: Google Trends-derived sentiment, EIA
  weekly storage surprises, USDA WASDE deviations from consensus.
  Already-ingested in Stage 2; Stage 5 turns the data into signals.

## Open questions for Stage 5's `decisions.md`

1. **Vol surface data source**: pin the source choice early
   (CME end-of-day vs Bloomberg vs paid academic feed). Affects
   what kind of vol-surface signals are even feasible.
2. **BVAR prior**: literature standard is Minnesota prior; verify
   it makes sense on the small US-macro indicator set we have.
3. **Alt-data confidence**: Google Trends is noisy; how much
   smoothing before the signal is usable?

## API + dashboard work for Stage 5

The heatmap goes from 7 columns to ~10. The existing checkbox-
selection control needs to land before adding the new columns —
once the table reaches 10 columns wide, default-on-everything stops
fitting on a 1440px desktop.

New API endpoints expected:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/signals/vol_surface/slices?as_of=&instrument=` | Latest IV slice + SVI fit |
| GET | `/api/v1/signals/nowcasting/projections?as_of=` | Current nowcast + uncertainty |
| GET | `/api/v1/signals/alt_data/sentiment?as_of=` | Latest alt-data signal aggregates |

## What's drifting / worth watching

- **`uv.lock`** is now in sync (Stage 4B Phase 0.1). Keep it that
  way: any dep change to `pyproject.toml` should be followed by
  `uv sync` and a lockfile commit in the same PR.
- **`signal_values.metadata`** JSONB row size has grown again
  (factor exposure carries `factor_loadings` and `factor_zscores`
  per row). If query times start drifting, consider materialising
  hot metadata fields as columns (Stage 4A noted this for
  positioning's `is_extreme` / `is_fresh_data`).
- **DFM convergence**: still untested against real data. Stage 5's
  yfinance backfill will surface the first real DFM `fit_failed`
  log lines; consider adding alerting on consecutive failures.

## Stage 5 commit checkpoint cadence

- After vol-surface family
- After nowcasting family
- After alt-data signals
- After API + dashboard extensions (vol-surface + nowcasting
  pages, checkbox column selector)
- After tests + notes
