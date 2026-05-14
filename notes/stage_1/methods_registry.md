# Stage 1 — methods registered

**No methods are registered in Stage 1.**

Stage 1 ships only the framework. No business logic exists yet, so no
algorithm needs a `Method` subclass. The registry table
(`system.methods_registry`) is created by migration `0001` and is empty
after `python make.py setup`.

This file's job is to explain how Stages 3+ must register methods, and to
walk through a concrete worked example for Stage 4 (factor exposure).

## Lifecycle recap

```
DEVELOPMENT ──► SHADOW ──► PRODUCTION
       │                       │
       └─► BASELINE ◄───────────┘   (BASELINE may also be the very first PRODUCTION)
                ▲
                │ superseded
                │
            DEPRECATED
```

- `BASELINE` is the conservative driving method.
- `SHADOW` is the candidate enhancement.
- `PRODUCTION` is the promoted enhancement. At most one per component.
- Promotion is **manual** via `POST /api/v1/methods/{id}/status`.
- Promoting a method automatically demotes the previously-driving method
  to `DEPRECATED` (the registry enforces this).

## How to register

```python
from macro_trader.methods import (
    Method, MethodMetadata, MethodStatus,
    register_method,
)
from macro_trader.db.engine import get_session

class MyBaseline(Method[InputT, OutputT]):
    metadata = MethodMetadata(
        method_id="<component>.<algorithm>.v<version>",   # stable globally
        component="<component_name>",                     # matches comparator's component
        name="<human readable>",
        version="1.0.0",
        description="One paragraph: what it does, assumptions, limits.",
        references=["doi://...", "docs/decisions/..."],
    )
    def fit(self, data) -> None: ...
    def predict(self, data) -> OutputT: ...
    def serialize(self) -> bytes: ...
    @classmethod
    def deserialize(cls, blob) -> "MyBaseline": ...

with get_session() as session:
    register_method(MyBaseline(), MethodStatus.BASELINE,
                    session=session, reason="first baseline for this component")
```

The first registration writes one row to `system.methods_registry` and one
row to `system.method_status_history` (`old_status = NULL`,
`new_status = BASELINE`).

## Status discipline

| Transition | When | Who |
| --- | --- | --- |
| → DEVELOPMENT | First commit of the method | author |
| DEVELOPMENT → BASELINE | First production-ready version, no enhancement competing | author |
| DEVELOPMENT → SHADOW | A baseline already exists; this is the candidate | author |
| SHADOW → PRODUCTION | Promotion criteria met; manual decision based on evidence | admin |
| PRODUCTION / BASELINE → DEPRECATED | Superseded by a new PRODUCTION | registry (automatic) |

## Worked example — Stage 4, factor exposure

This is the canonical pattern; Stages 3, 5, 6, 7, 8, 9 follow it.

### Files involved

```
src/macro_trader/signals/factor_exposure/
├── __init__.py
├── ols.py             ← MyBaseline (OLS)
├── causal_forest.py   ← MyEnhancement (Causal Forest)
└── comparator.py      ← FactorExposureComparator
orchestration/assets/factor_exposure.py     ← daily Dagster comparator asset
tests/integration/test_factor_exposure.py   ← end-to-end test
notes/stage_4/methods_registry.md           ← Stage 4's analog of this file
```

### Registration code

```python
# orchestration/setup_methods.py (run at deployment-time / Dagster init)
from macro_trader.methods import register_method, MethodStatus
from macro_trader.db.engine import get_session
from macro_trader.signals.factor_exposure.ols import OLSExposure
from macro_trader.signals.factor_exposure.causal_forest import CausalForestExposure

with get_session() as session:
    register_method(OLSExposure(), MethodStatus.BASELINE, session=session,
                    reason="Stage 4 — initial OLS factor exposure baseline")
    register_method(CausalForestExposure(), MethodStatus.SHADOW, session=session,
                    reason="Stage 4 — Causal Forest enhancement")
```

### Comparator

```python
# src/macro_trader/signals/factor_exposure/comparator.py
from macro_trader.methods import MethodComparator

class FactorExposureComparator(MethodComparator[FactorPanel, ExposureMatrix]):
    def __init__(self):
        super().__init__(component="macro_factor_exposure")

    def _compute_metrics(self, output_a, output_b, *, data):
        # Convention: <metric>_a is baseline, <metric>_b is shadow.
        return {
            "rmse_a":         rmse(output_a, data.realized),
            "rmse_b":         rmse(output_b, data.realized),
            "sharpe_a":       sharpe_of_exposure(output_a, data.returns),
            "sharpe_b":       sharpe_of_exposure(output_b, data.returns),
            "stability_a":    1.0 - param_jitter(output_a),
            "stability_b":    1.0 - param_jitter(output_b),
        }
```

### Daily Dagster asset

```python
# orchestration/assets/factor_exposure.py
@asset(group_name="factor_exposure")
def factor_exposure_comparison(context: AssetExecutionContext) -> None:
    a = get_production("macro_factor_exposure")    # the current driver
    b_candidates = get_shadows("macro_factor_exposure")
    comparator = FactorExposureComparator()
    data = load_panel(period=context.partition_key)
    with get_session() as session:
        for b in b_candidates:
            result = comparator.compare(a, b, data,
                                        period_start=..., period_end=...,
                                        session=session)
            context.log.info(f"compared {a.metadata.method_id} vs {b.metadata.method_id}: {result.metrics}")
```

### Promotion review

After ~6 months of daily comparisons (180 days × ~120 trading days
filtered for valid data), an analyst:

1. Opens the dashboard `/methods` page, filters to
   `component=macro_factor_exposure`, sees both methods listed.
2. Clicks into the shadow's history and inspects the recent comparison
   summary cards: relative improvement on `rmse`, `sharpe`, `stability`.
3. Calls the eligibility checker (in code, via a small CLI, or by adding
   `/api/v1/methods/{id}/promotion-eligibility` in Stage 11):
   ```python
   eligible, evidence = evaluate_promotion(
       "factor_exposure.cf.v1",
       PromotionCriteria(
           component="macro_factor_exposure",
           min_shadow_period_days=180,
           min_comparison_runs=120,
           required_improvements=["sharpe", "stability"],
           improvement_threshold=0.05,
       ),
       session=session,
   )
   ```
4. If eligible, copies the `evidence` dict into the PR description (or
   incident review doc) and posts:
   ```
   POST /api/v1/methods/factor_exposure.cf.v1/status
   { "status": "production", "reason": "Promoted 2026-Q4 after 180-day shadow with mean Sharpe +8%, stability +6%; PR #213." }
   ```
5. The registry automatically demotes `factor_exposure.ols.v1` to
   `deprecated`. From the next Dagster tick, the Causal Forest drives
   factor exposure decisions.

## Auditing

- Every status change is in `system.method_status_history`. It's
  append-only by convention. Reasons are required (`POST` payload
  validation rejects empty strings).
- Every comparison is in `system.method_comparisons`. The Dashboard
  Methods page renders them grouped by component and time.
- Removing a deprecated method from the registry should be very rare and
  always paired with a backup of the row.
