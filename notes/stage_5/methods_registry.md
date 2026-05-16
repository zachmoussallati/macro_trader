# Stage 5 — methods registered

Seven new methods bring the registry to its final Stage-5 state.

| method_id | component | status | notes |
| --- | --- | --- | --- |
| `alt_data.eia_storage.v1` | alt_data_signal | BASELINE | Covers CL/BZ/NG/HO/RB; 5yr seasonal surprise, 156wk z-score. |
| `alt_data.usda_wasde.v1` | alt_data_signal | BASELINE | Covers ZC/ZS/ZW; MoM production change, 36-report z-score. |
| `alt_data.google_trends.v1` | alt_data_signal | BASELINE | Contrarian; per-query 7-day EWM, 90-day z-score. |
| `nowcasting.ols_ar.v1` | nowcasting_signal | BASELINE | Per-release linear regression; designated production. |
| `nowcasting.bvar.v1` | nowcasting_signal | SHADOW | Conjugate Normal-Inverse-Gamma posterior, Minnesota-flavoured prior. |
| `vol_surface.raw.v1` | vol_surface_signal | BASELINE | Raw chain metrics; PROMOTION BLOCKED until paid-data history exists. |
| `vol_surface.svi.v1` | vol_surface_signal | SHADOW | Spline-fitted surface with calendar-arb check; full SVI deferred (see decisions.md §4). PROMOTION BLOCKED. |

## Why vol-surface methods cannot be promoted in Stage 5

Both `vol_surface.raw.v1` and `vol_surface.svi.v1` carry the
metadata flag `historical_backtest_supported: False` (Stage 5
decisions.md §2). The Stage 9 backtester will skip the family when
computing historical metrics — therefore no comparator data
accumulates over time, therefore the standard promotion criteria
(`min_comparison_runs`, `value_correlation` improvement, etc.)
cannot be satisfied.

This is documented as the central architectural constraint of the
yfinance pre-decision. Promotion lifts when:

1. A paid options-data provider replaces yfinance via the
   `OptionsChainIngester` subclass pattern (Stage 5 decisions.md §2).
2. Historical chains backfill into `market_data.options_chains`.
3. The `historical_backtest_supported` flag flips to `True` on the
   methods.
4. Daily comparator runs accumulate for the `min_shadow_period_days`
   period defined in the promotion criteria template.

## Designated-method config after Stage 5

```yaml
signals:
  designated_per_component:
    trend_signal:           trend.ensemble.v1
    carry_signal:           carry.spot_proxy.v1
    value_signal:           value.zscore.v1
    positioning_signal:     positioning.cot_zscore.v1
    dislocation_signal:     dislocation.pca.v1
    factor_exposure_signal: factor_exposure.ols.v1
    catalyst_signal:        catalyst.event_study.v1
    alt_data_signal:        alt_data.eia_storage.v1
    vol_surface_signal:     vol_surface.raw.v1
    nowcasting_signal:      nowcasting.ols_ar.v1
```

## Updated promotion criteria templates

```python
PromotionCriteria(
    component="vol_surface_signal",
    # BLOCKED — requires paid options data first.
    min_shadow_period_days=180,
    min_comparison_runs=24,
    required_improvements=[
        "sharpe", "max_dd_ratio",
        "value_correlation",
        "calendar_arbitrage_violations",  # SVI must reduce violations
    ],
    improvement_threshold=0.05,
)

PromotionCriteria(
    component="nowcasting_signal",
    min_shadow_period_days=180,
    min_comparison_runs=24,
    required_improvements=[
        "sharpe", "max_dd_ratio",
        "nowcast_rmse",            # BVAR must reduce nowcast RMSE
        "calibration",             # posterior predictive coverage
    ],
    improvement_threshold=0.05,
)
```

Alt-data signals are all BASELINE under a single component; their
promotion path is collective (composite scoring in Stage 7) rather
than individual.

## Stable-as-of-Stage-5 interfaces

Stage 6+ can rely on:

- All 10 signal families' `compute()` returning standard
  `list[SignalOutput]`.
- `OptionsChainIngester` ABC pattern in
  `src/macro_trader/signals/vol_surface/ingestion/base.py` for any
  source-agnostic data ingestion (paid options, paid macro feeds,
  etc.).
- Black-Scholes pricing module in
  `src/macro_trader/signals/vol_surface/pricing.py` — pure
  functions, reusable anywhere Greeks or IV solving is needed
  (Stage 12 execution layer will use these for option order
  pricing).
- Conjugate Normal-Inverse-Gamma BVAR in
  `src/macro_trader/signals/nowcasting/bvar.py` — reusable for any
  small-sample Bayesian regression Stage 6+ needs.
- Per-method `_state` serialisation pattern (pickle + zlib above
  32KB) consistent across dislocation / factor_exposure / catalyst
  / nowcasting refit modules.
