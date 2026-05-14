# Stage 3 — operational extras

(Supplement to `usage.md`. Stage 1/2 documented core flows; this file
adds the recipes specific to signal operations that didn't fit the main
usage doc.)

## Force-rebuild signal values for the trailing year

```python
from datetime import timedelta
from macro_trader.db.engine import get_session
from macro_trader.signals.trend.runner import run_daily_trend
from macro_trader.signals.carry.runner import run_daily_carry
from macro_trader.signals.value.runner import run_daily_value

with get_session() as s:
    run_daily_trend(s, window_days=365)
    run_daily_carry(s, window_days=365)
    run_daily_value(s, window_days=365)
```

UPSERT idempotency means re-running is safe.

## Compare two methods at a fixed snapshot

```python
from datetime import datetime, timezone, timedelta
from macro_trader.signals.base import SignalInput
from macro_trader.signals.trend.methods import TrendEnsemble, HPFilterTrend
from macro_trader.signals.trend.comparator import TrendSignalComparator
from macro_trader.db.engine import get_session

with get_session() as s:
    sig_in = SignalInput(
        instrument_ids=["CL", "BZ", "GC", "ZC"],
        as_of=datetime.now(timezone.utc),
        start=datetime.now(timezone.utc) - timedelta(days=30),
        end=datetime.now(timezone.utc),
        extras={"session": s},
    )
    comparator = TrendSignalComparator()
    result = comparator.compare(
        TrendEnsemble(),
        HPFilterTrend(),
        sig_in,
        period_start=sig_in.start,
        period_end=sig_in.end,
        notes="ad-hoc",
        session=s,
    )
    print(result.metrics)
```

## Inspect what the heatmap will show

```bash
curl http://localhost:8000/api/v1/signals/heatmap | jq '
  group_by(.component) | map({
    component: .[0].component,
    cells: map({inst: .instrument_id, raw: .raw_value, z: .zscore, conf: .confidence})
  })
'
```

## Common failure modes

### "No signal values yet" on detail page

- Check `system.heartbeat` for `source = "signals.trend"` etc.
  ```sql
  SELECT timestamp, source FROM system.heartbeat
  WHERE source LIKE 'signals.%' ORDER BY id DESC LIMIT 5;
  ```
- If the most recent is hours old: trigger
  `compute_all_signals_job` in Dagster.
- If there's no row at all: methods may not be registered. Restart
  Dagster (`register_all_methods` runs on code-location load) or run:
  ```python
  from macro_trader.methods.setup import register_all_methods
  from macro_trader.db.engine import get_session
  with get_session() as s:
      register_all_methods(s)
  ```

### All ranks come back at 0.5

The `cross_sectional_rank` helper falls back to 0.5 for sub-classes
with a single instrument. If you're seeing 0.5 across the board, the
panel only contains one instrument — check the `instrument_ids` list
you passed in.

### Confidence is 0 for every output

Confidence is `fraction of non-NaN observations in trailing 252d
window`. Zero means there's no historical data for the instrument. Run
the relevant ingester (e.g., `ingest_yfinance_bars_job`) and re-run the
signal job.

## Promotion path for the HP-filter shadow (post-Stage 9)

```python
from macro_trader.methods.promotion import PromotionCriteria, evaluate_promotion
from macro_trader.db.engine import get_session

with get_session() as s:
    criteria = PromotionCriteria(
        component="trend_signal",
        min_shadow_period_days=180,
        min_comparison_runs=120,
        required_improvements=["rolling_sharpe_oos"],  # Stage 9 adds this
        improvement_threshold=0.05,
    )
    eligible, evidence = evaluate_promotion(
        "trend.hp_filter.v1", criteria, session=s,
    )
    print(eligible, evidence)
```

Once eligible, an admin POSTs `/api/v1/methods/trend.hp_filter.v1/status`
with `status=production`. The registry automatically demotes the
previous PRODUCTION method to DEPRECATED.
