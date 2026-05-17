# Stage 6 — architecture

Final state: 10 signal families + 5 regime methods. Regime classifier
is the system's first meta-layer (operates over signals' history, not
over price data directly).

## File-by-file

```
alembic/versions/
└── 0005_regime_states_and_attribution.py  # NEW migration

src/macro_trader/db/models/
└── regime.py                                # NEW: RegimeState + RegimeAttribution

src/macro_trader/regime/                     # NEW package
├── __init__.py             # NAMED_REGIMES tuple
├── features.py             # build_regime_features() — 10-feature panel
├── labeling.py             # centroid-anchored Hungarian assignment
├── methods.py              # 5 methods (Rules / GMM / HMM / MSVAR / BOCPD)
├── comparator.py           # RegimeClassifierComparator
├── attribution.py          # compute_attribution()
├── runner.py               # run_daily_regime_classification()
├── refit.py                # weekly + quarterly refit
└── register.py             # register all 5 methods

orchestration/assets/
└── regime.py               # 3 NEW Dagster assets

orchestration/definitions.py # +3 jobs + 4 schedules

api/routers/
└── regime.py               # NEW: 5 regime endpoints

frontend/src/
├── App.tsx                 # +/regime route
├── api/client.ts           # +5 typed shapes + 5 method bindings
└── pages/Regime.tsx        # NEW page

tests/
├── unit/regime/
│   ├── test_labeling.py    # NEW: Hungarian assignment correctness
│   └── test_methods.py     # NEW: rules + GMM + MSVAR metadata + BOCPD math
└── (integration tests deferred to Stage 7 — need Postgres)
```

## Schema additions (alembic 0005)

```
regime.regime_states               -- daily classification per method
    method_id          PK
    value_ts           PK (hypertable partition)
    observation_ts     PK
    label              VARCHAR(64)
    probability_vector JSONB
    confidence, transition_prob, days_in_regime
    state_metadata     JSONB
    lineage_id         UUID

regime.regime_attribution          -- weekly perf by (regime, signal)
    regime_method_id   PK
    regime_label       PK
    signal_method_id   PK
    value_ts           PK (hypertable partition)
    n_observations, mean_return, sharpe, hit_rate
    attribution_metadata JSONB
```

Both hypertables on `value_ts` when TimescaleDB extension is
present; vanilla Postgres falls back to plain tables.

## Final signal+regime stack

```mermaid
flowchart TB
    subgraph DATA[Stage 2 data]
        bars[market_data.daily_bars]
        macro[macro_data.series_observations]
        cot[positioning.cot_weekly]
        cal[macro_data.calendar_events]
        eia[alt_data.eia_inventory]
        usda[alt_data.usda_reports]
        gt[alt_data.google_trends]
        oc[market_data.options_chains]
    end
    subgraph SIG[10 signal families]
        s1[trend] & s2[carry] & s3[value] & s4[positioning] & s5[dislocation]
        s6[factor_exposure] & s7[catalyst] & s8[alt_data] & s9[nowcasting] & s10[vol_surface]
    end
    subgraph REG[Regime classifier — 5 methods]
        rRules[regime.rules.v1 BASELINE]
        rGMM[regime.gmm.v1]
        rHMM[regime.hmm.v1]
        rMSVAR[regime.msvar.v1]
        rBOCPD[regime.bocpd.v1]
    end
    subgraph ATTRIB[(regime.regime_attribution)]
        att[per-(regime,signal) sharpe + hit_rate]
    end
    DATA --> SIG
    SIG --> svalues[(signals.signal_values)]
    REG --> rstates[(regime.regime_states)]
    DATA -.factor panel.-> REG
    svalues --> att
    rstates --> att
    att -.Stage 7 weights.-> COMP[Stage 7 composite scoring]
```

## Schedule cadence after Stage 6

```
Daily    23:30 UTC  compute_all_signals_job
                    regime_classification_job
Weekly   00:00 UTC  dislocation_models_refit
         01:00 UTC  factor_exposure_models_refit
         02:00 UTC  catalyst_models_refit
         03:00 UTC  nowcasting_models_refit
         04:00 UTC  regime_refit_job (GMM + quarterly HMM/MSVAR)
         05:00 UTC  regime_attribution_job
```

## API surface (Stage 6 additions)

```
GET  /api/v1/regime/methods                   # registered classifiers
GET  /api/v1/regime/current?method_id=...     # latest classification
GET  /api/v1/regime/history?method_id=...     # per-day label time series
GET  /api/v1/regime/attribution?regime_method&signal_method
GET  /api/v1/regime/changepoints?from&to      # BOCPD changepoint probs
```

## Frontend addition

- `/regime` page: method selector + current-regime card (label,
  probability bar, days_in_regime, confidence) + recent-history
  table + attribution heatmap (rows = signal methods, columns = 5
  named regimes, cell = Sharpe with diverging colour).
- Vitest smoke (`Regime.test.tsx`) asserts header + empty-state UI.
