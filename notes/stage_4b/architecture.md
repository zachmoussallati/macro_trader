# Stage 4B — architecture

Practical "where do I find X?" map for what was added.

## Phase 0 (housekeeping)

- **uv.lock resync** (`uv sync --extra dev`) for the Stage 4A
  `respx>=0.21.0` drift.
- **Dislocation weekly refit** (Phase 0.2): see
  `src/macro_trader/signals/dislocation/refit.py` and the new
  `dislocation_models_refit` Dagster asset (Sunday 00:00 UTC). The
  daily `signal_dislocation` asset now `AssetIn`-depends on it.
  PCA's `align_signs_to(prior_state)` lives on the method.
- **Sub-class reseed** (Phase 0.3): `instruments_seed.py` updated to
  `crude_oil` / `refined_products` / `base_metals` /
  `precious_metals` / `grains` and `CrossSectionalValue.class_column`
  default flipped to `"sub_class"`.

## Phase 1 — factor exposure family

```
src/macro_trader/signals/factor_exposure/
├── __init__.py
├── factors.py        # FactorSpec, build_factor_panel, latest_factor_zscores
├── methods.py        # OLSFactorExposure, RandomForestFactorExposure,
│                     # CausalForestFactorExposure (gated on EconML)
├── comparator.py     # FactorExposureComparator
├── refit.py          # weekly refit + serialize/deserialize
├── runner.py         # daily inference; reads cached state via load_*_state
└── register.py
```

- Six factors (factors.py): growth/inflation/liquidity/usd/oil/risk_on,
  each derived from a FRED series via a transform + 252-day rolling
  z-score. Two new FRED series added: `DFII2`, `VIXCLS`.
- Three methods, all with `_state` / `fit_on_panels` /
  `predict_at` / `serialize` / `deserialize` — same lifecycle as the
  Stage 4A dislocation methods.
- New Dagster assets:
  - `factor_exposure_models_refit` (Sunday 01:00 UTC, staggered
    after dislocation)
  - `signal_factor_exposure` (daily; depends on the refit)
- New `factor_exposure_refit_job` for ad-hoc reruns.
- API:
  - `GET /api/v1/signals/factor_exposure/factors` (latest factor
    z-scores)
  - `GET /api/v1/signals/factor_exposure/loadings?method_id=&as_of=`
    (per-instrument loadings + signal snapshot)

## Phase 2 — catalyst sensitivity family

```
src/macro_trader/signals/catalyst/
├── __init__.py
├── events.py         # historical_event_returns, estimate_sensitivities,
│                     # time_decay_weight, upcoming_score
├── methods.py        # EventStudyCatalyst, CausalCatalyst (placeholder)
├── comparator.py     # CatalystSignalComparator
├── refit.py          # weekly refit; sensitivities to serialized_blob
├── runner.py         # daily inference
└── register.py
```

- First real consumer of `macro_trader.calendar.api.events_in_window`.
- Daily forward score = sum_{events ahead} sensitivity * linear
  time-decay (10-day window).
- New Dagster assets:
  - `catalyst_models_refit` (Sunday 02:00 UTC, staggered after
    factor exposure)
  - `signal_catalyst` (daily; depends on the refit + calendar)
- New `catalyst_refit_job` for ad-hoc reruns.
- API: `GET /api/v1/signals/catalyst/events?as_of=&days_ahead=` —
  upcoming events the catalyst signal will fold into per-instrument
  scores.

## Cross-cutting after Stage 4B

- `system.methods_registry.serialized_blob` is now actively used by
  three families (dislocation, factor_exposure, catalyst). Helpers
  in `macro_trader.methods.registry`: `store_serialized_blob`,
  `load_serialized_blob`. Each refit module wraps these with
  optional zlib compression (32 KB threshold, `b"ZLIB"` magic).
- `COMPONENTS_FOR_HEATMAP` in `api/routers/signals.py` now lists 7
  components. Frontend's `COMPONENT_ORDER` already extends to 5 and
  needs to grow to 7 in Stage 4C (see tradeoffs.md).
- `compute_all_signals_job` materialises all 7 daily signal assets.
  Schedule cadence:

```
Daily:    23:30 UTC  compute_all_signals_job
Weekly:   00:00 UTC  dislocation_models_refit       (Sunday)
          01:00 UTC  factor_exposure_models_refit   (Sunday)
          02:00 UTC  catalyst_models_refit          (Sunday)
```

## Mermaid: Stage 4B connectivity

```mermaid
flowchart LR
    subgraph DATA[Stage 2 data]
        bars[market_data.daily_bars]
        macro[macro_data.series_observations]
        cal[macro_data.calendar_events]
    end

    subgraph FACTORS[factor_exposure]
        ff[factors.build_factor_panel]
        ols[OLSFactorExposure BASELINE]
        rf[RandomForestFactorExposure SHADOW]
        cf[CausalForestFactorExposure SHADOW]
    end

    subgraph CATALYST[catalyst]
        ev[events.historical_event_returns]
        es[EventStudyCatalyst BASELINE]
        cc[CausalCatalyst SHADOW placeholder]
    end

    subgraph BLOB[(system.methods_registry.serialized_blob)]
        b1[dislocation.pca/dfm]
        b2[factor_exposure.ols/rf/cf]
        b3[catalyst.event_study/causal]
    end

    bars --> ff
    macro --> ff
    ff --> ols & rf & cf
    cal --> ev
    bars --> ev
    ev --> es & cc

    ols & rf & cf -.-> b2
    es & cc -.-> b3

    ols & rf & cf --> sv[(signals.signal_values)]
    es & cc --> sv

    ols & rf -.-> CMPF[(comparator: factor_exposure)]
    es & cc -.-> CMPC[(comparator: catalyst)]
```
