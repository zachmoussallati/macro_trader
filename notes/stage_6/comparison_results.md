# Stage 6 — comparison results

## What `RegimeClassifierComparator` measures

| metric | what it is |
| --- | --- |
| `label_agreement` | Fraction of overlapping value_ts where both methods assigned the same label. **Load-bearing metric for promotion**. |
| `probability_correlation` | Mean Pearson correlation across per-regime probability series. High when methods agree on relative regime likelihoods even if argmax differs. |
| `transition_alignment` | Fraction of one method's transitions that occur within ±3 days of the other's. Captures "do they detect the *same* regime shifts even if they pick different labels?" |
| `rolling_label_stability_a` / `_b` | Per-method day-to-day label persistence. Low values = jumpy classifier. |
| `confidence_a` / `_b` | Mean confidence per method. |

The comparator skips the default `_compute_stability` perturbation
(signal-style) because regime methods don't accept perturbed input
the same way; per-regime stability is captured via
`rolling_label_stability`.

## Expected ranges

Until daily classification runs accumulate, the comparator can't
produce meaningful numbers. Once it has ≥30 days of overlap:

- **Rules vs GMM**: expect `label_agreement` ≈ 0.55–0.75. GMM
  picks up smooth boundary regions the rules miss; rules can be
  too sharp.
- **Rules vs HMM**: expect `label_agreement` ≈ 0.55–0.75 with
  *higher* `rolling_label_stability_b` than rules (HMM's Markov
  smoothing reduces flicker).
- **GMM vs HMM**: expect `label_agreement` > 0.70 (both data-driven,
  similar feature space). When they disagree it's usually around
  transition periods where HMM's Markov prior keeps it in the old
  regime longer.
- **MS-VAR vs HMM**: expect lower agreement (~0.50). MS-VAR
  classifies based on PC1 dynamics; HMM uses the full feature
  vector.
- **BOCPD vs rules**: BOCPD inherits rules labels by design;
  `label_agreement` should be 1.0. The interesting signal is the
  changepoint probability, not the label.
- **`transition_alignment`**: expect 0.3–0.6 across pairs. Methods
  rarely detect the *exact* same day for a regime shift, but they
  often agree within a few days.

## Stage 6 — no real numbers yet

The comparator runs daily as part of `regime_classification_job` but
needs accumulated history. After ~30 days of production runs:

- Check the Methods page's ShadowDifferentiationBadge against
  `regime_classifier`. A "no data" pill turns into a green
  "shadow differentiated" or muted "near identical" once daily
  comparisons accumulate.
- Query directly:

```bash
curl 'http://localhost:8000/api/v1/signals/comparisons?component=regime_classifier' | jq
```

## What attribution will show (Stage 7's input)

Once `regime_attribution_compute` runs a few times, the heatmap on
`/regime` will populate. Empirical expectations (US data, broad
universe):

- **Trend signals** strong in `risk_on_growth` (long-term uptrends
  pay) and `risk_off_defensive` (clean down-trends in equities);
  weak in `carry_friendly` (ranging markets).
- **Mean-reversion (value)** strong in `carry_friendly` (mean-
  reverting markets pay); weak in trending regimes.
- **Catalyst signals** strong in `vol_spike` (events drive prices);
  weak otherwise.
- **Positioning signals** mostly regime-agnostic; small Sharpe
  uplifts in extremes.
- **Vol surface signals** can't accumulate attribution until paid
  options data lands (Stage 5's
  `historical_backtest_supported: false` flag).

Stage 7 composite scoring uses these Sharpes to weight signals.
