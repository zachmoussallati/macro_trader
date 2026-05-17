# Stage 6 — methods registered

Five new methods under the `regime_classifier` component.

| method_id | status | refit | notes |
| --- | --- | --- | --- |
| `regime.rules.v1` | BASELINE | stateless | Designated production. Transparent decision tree on VIX + curve + USD + growth/inflation z. |
| `regime.gmm.v1` | SHADOW | weekly | sklearn `GaussianMixture(n_components=5, covariance_type="full")`. Centroid-anchored labels. |
| `regime.hmm.v1` | SHADOW (gated on hmmlearn) | quarterly | `hmmlearn.GaussianHMM`. Smoothing-only for now; filtering helper deferred to Stage 9. |
| `regime.msvar.v1` | SHADOW | quarterly | statsmodels `MarkovRegression` on PC1 (univariate fallback for full MS-VAR per Stage 6 prompt's allowance). |
| `regime.bocpd.v1` | SHADOW | stateless (online) | Adams & MacKay 2007. Label inherited from rules; novel output is `transition_prob` = P(r_t < 5 \| x_{1:t}). |

## Designated-method config

```yaml
signals:
  designated_per_component:
    # ... 10 signal families ...
    regime_classifier: regime.rules.v1
```

## Promotion criteria template (pending Stage 9 backtester)

```python
PromotionCriteria(
    component="regime_classifier",
    min_shadow_period_days=180,
    min_comparison_runs=24,
    required_improvements=[
        "label_agreement",      # high agreement with rules baseline
        "rolling_label_stability",  # shadow stays as stable or more
        "n_observations",       # enough comparator runs accumulated
    ],
    improvement_threshold=0.05,
)
```

The Stage 6 RegimeClassifierComparator's `label_agreement` is the
load-bearing metric here. A shadow that disagrees with the
baseline on >40% of days is suspicious; one that agrees on >95% is
not providing distinct information.

## Stable-as-of-Stage-6 interfaces

Stage 7 (signal combination) can rely on:

- All 5 regime methods produce daily rows in `regime.regime_states`.
- `RegimeState` carries both the categorical `label` and the full
  `probability_vector` JSONB.
- `regime.regime_attribution` populates weekly; one row per
  (regime_method_id, regime_label, signal_method_id) with
  `n_observations`, `mean_return`, `sharpe`, `hit_rate`.
- `signals.factor_exposure.factors.build_factor_panel` is the
  canonical macro factor input; the regime classifier reuses it +
  4 market indicators (vol/credit/curve/VIX).
- BOCPD's `transition_prob` (= P(r_t < 5)) is the canonical
  "changepoint just happened" indicator for any future
  alerting / regime-shift detection consumer.

## Stable-as-of-Stage-6 architectural promises

- **Centroid-anchored labelling** keeps named regimes stable across
  refits. Hungarian assignment in
  `regime.labeling.map_centroids_to_labels`.
- **Drift warnings** log when a new centroid moves >2 std from its
  matched prior centroid — the canonical "regime structure may have
  genuinely shifted" alert.
- **Filtering vs smoothing** distinction documented for HMM
  (decisions.md §6). Stage 9 backtester will need to override the
  smoothing-by-default behaviour for strict point-in-time backtests.
