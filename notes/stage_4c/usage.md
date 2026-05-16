# Stage 4C — usage

## Installing the `[ml]` extras

```bash
uv sync --extra dev --extra ml
```

This pulls EconML 0.16.0 plus its transitive deps (numba, llvmlite,
shap, sparse). It also forces sklearn down to 1.6.1 for EconML
compat. Without `--extra ml` the catalyst causal + factor exposure
CF methods skip cleanly at registration / refit / runner level and
their EconML-gated tests skip as well.

## Generating the backfill cache (one-time, requires API keys)

```bash
export FRED_API_KEY=...        # required
python -m tests.integration.fixtures.backfill
```

The script writes
`tests/data/backfill_504d.parquet` (~few-MB single multi-sheet file
covering 504 days of yfinance bars + FRED factor series). Once the
cache exists, `pytest -m real_data` runs every real-data
integration test; before it exists, those tests skip with a clear
"backfill cache not found" message.

To regenerate (e.g. after universe changes), delete the parquet
and re-run the same command.

## Visiting the new pages

```
/signals                    main heatmap (now 7 columns + checkbox selector)
/signals/positioning        per-instrument COT breakdown
/signals/dislocation        PCA vs DFM factor model inspector
/signals/factor_exposure    OLS / RF / CF factor exposure inspector
/signals/catalyst           upcoming events + per-instrument pressure
/methods                    registry table + shadow-differentiation badges
```

The main `/signals` page has a "Drill in:" nav row linking to each
of the four new pages. Each new page also has a "Back to signals"
button in its header.

## Heatmap column selector

Click the checkboxes above the heatmap to hide / show columns. The
selection persists across navigation and reloads via Zustand's
`persist` middleware (localStorage key
`macro-trader.signals-view`). "Show all" resets to default.

## Methods page differentiation badge

Each component card on `/methods` shows a small badge with the
latest `value_correlation_a_b` from `/signals/comparisons?component=...`.
Read it as:

- **near identical (corr ≥ 0.95)** — shadow likely just retracing
  the baseline; investigate before promoting.
- **shadow differentiated (0.5 ≤ corr < 0.95)** — the desired
  range; shadow producing a related-but-distinct signal.
- **diverged (corr < 0.5)** — verify the methodology rather than
  promoting blindly.
- **no data / loading / error** — visible when no comparator runs
  exist yet or the API call failed.

Hover the badge for tooltip text explaining the threshold
rationale.

## Investigating CATE-derived catalyst signals

The catalyst page's method selector now offers
`catalyst.event_study.v1` and `catalyst.causal.v1`. After Stage
4C's CATE landed, the two methods produce *different* forward
scores when sufficient (instrument, event_subject) pairs have ≥15
historical events; for under-served pairs the causal method falls
back to the event-study sensitivity (logged in
`metadata.fallback_pairs`).

Workflow when a high-pressure score appears:

1. Compare the two methods' forward scores for the same instrument
   on the catalyst page. If they agree, both methodologies see the
   catalyst risk.
2. If they disagree, drill into the historical events table for
   the instrument and read the per-event log returns. Different
   sensitivities by macro regime are exactly what the causal
   method is supposed to surface.
3. Check `/methods` for the catalyst component's differentiation
   badge — corr near 1.0 means the two methods aren't giving you
   independent reads (most likely because most pairs fell back to
   event-study).

## Running tests with the `[ml]` extras

```bash
# Full backend suite (EconML-gated tests now run):
python make.py test

# Only the EconML-touching tests:
python make.py test -- tests/unit/signals/factor_exposure/test_causal_forest.py \
                       tests/unit/signals/catalyst/test_methods.py

# Real-data integration tests (require backfill cache):
python make.py test -- -m real_data
```

## Refitting the catalyst causal model manually

```python
from macro_trader.db.engine import get_sessionmaker
from macro_trader.signals.catalyst.refit import refit_causal

sf = get_sessionmaker()
with sf() as session:
    result = refit_causal(session)
    print(result)
    session.commit()
```

`result.blob_size_bytes` tells you how big the cached state is (the
zlib threshold is 32 KB; below that the blob is stored uncompressed
inside Postgres TOAST).
