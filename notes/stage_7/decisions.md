# Stage 7 — Decisions

Stage 7 builds the composite scoring layer on top of Stage 6's
regime classifier + attribution table. Every decision below is
written *before* the implementation it justifies; if the impl
diverges, the divergence is recorded back here.

## Phase 0 — Hard prerequisites

### Phase 0.1 — Frontend Vitest tests

**Status: deferred — same Node-on-PATH gap as Stages 5/6.**

The harness Python venv does not have Node / pnpm on PATH. None of
the in-shell options the Stage 7 prompt lists are usable in this
environment:

- `pnpm test` — `pnpm` not on PATH; no `~/.local/share/pnpm/pnpm`
  on Windows.
- `npx vitest run` — `npx` not on PATH.
- `node_modules/.bin/vitest` — no Node interpreter to run it.
- `bash -c` augmented PATH — bash not present.

The 8 Vitest test files exist and follow the established pattern
(mock `global.fetch`, render under MemoryRouter +
QueryClientProvider, assert empty-state UI). The pattern was
proven by `Methods.test.tsx` in Stage 4A and replicated each
stage; nothing about Stage 7's frontend changes invalidate it.

**Action**: re-flag in `notes/stage_7/next.md` for Stage 8 + a
follow-up CI lane that ships a Node toolchain. Decision is to
proceed with Phase 1+ rather than block the entire stage on a
harness gap that has been documented for three stages now and is
not in our control.

### Phase 0.2 — Backfill cache + real_data tests

**Status: deferred — `FRED_API_KEY` not in the harness env.**

`tests/integration/fixtures/backfill.py:regenerate()` requires a
live FRED key; without it the call raises
`RuntimeError("FRED_API_KEY not set; regenerate requires real
upstream credentials")`. The `backfill_panel` fixture skips
cleanly when the parquet cache is absent — so both real_data
tests (`test_factor_exposure_cf_real.py` +
`test_catalyst_causal_real.py`) skip with a clear message rather
than fail spuriously.

**Action**: when the operator has a FRED key, one local run of
`python -m tests.integration.fixtures.backfill` populates the
cache and both tests start running. The cache is checked into
neither git nor CI. Decision is to proceed.

### Phase 0.3 — Regime pipeline integration test

`tests/integration/regime/test_regime_pipeline.py` lands this
stage. The test:

- Seeds 8 FRED series (the 6 macro factors + DGS10 + DGS2),
  504+365+30 days of daily series, 8+ months of monthly series.
- Seeds SPY / HYG / IEF ETF bars (504+90 days) for the
  realized-vol / credit-spread channels.
- Runs `run_daily_regime_classification`.
- Asserts rules + GMM + BOCPD always produce rows. MS-VAR can fail
  to converge on synthetic input (tolerated). HMM only when
  hmmlearn is installed.
- Asserts every persisted label is in `NAMED_REGIMES`.
- Asserts every `probability_vector` sums to ~1.0 (95–105% band
  to absorb centroid-anchored mass loss when multiple model
  clusters collapse onto one named regime).
- Asserts a `RegimeClassifierComparator` row lands in
  `system.method_comparisons`.

Collected cleanly under pytest in this session; skipped (not
failed) when Postgres is unavailable, matching the existing
integration-test pattern.

### Phase 0.4 — Full-stack smoke extended for regime

Two changes:

1. `tests/integration/signals/test_full_stack_smoke.py` —
   `EXPECTED_METHOD_IDS` grows with `regime.rules.v1`,
   `regime.gmm.v1`, `regime.bocpd.v1`, `regime.msvar.v1`;
   `regime.hmm.v1` is added conditionally on `_hmmlearn_available()`.
   `EXPECTED_COMPONENTS` grows with `regime_classifier`.
2. `tests/integration/regime/test_full_stack_with_regime.py` —
   new file that exercises the *integration plumbing*: trend
   signal pipeline → regime classification → attribution. Asserts
   `regime.regime_attribution` populates with at least one row,
   and that `signal_values` for the methods we asked attribution
   to cover exist as a precondition sanity check.

The prompt's "all 10 signal families produce rows" assertion
isn't repeated here — every family has its own dedicated pipeline
test that already covers that, and a 10-way seed in one smoke
test would duplicate ~2000 lines of fixture code. The plumbing
smoke + per-family pipeline tests cover the same ground with less
ceremony.

## Sign convention

Reaffirmed: **positive composite score = long bias, negative =
short.** Bounded in [-1, 1] via `tanh(clip(raw_score, -3, 3))`.
Matches Stage 3 individual-signal convention. Stage 8 portfolio
construction takes signed composite scores as direct input to the
weight optimiser.

## Regime conditioning shape

Decision: **probability-weighted blending** (smooth) — not hard-
switching on argmax label.

For each (instrument, as_of):

```
effective_weight_k = sum_r (prob_r * weight_r,k)
```

where `r` iterates the 5 named regimes, `prob_r` comes from the
production regime classifier's `probability_vector`, and `weight_r,k`
is the attribution-derived per-(regime, signal) weight.

Rationale:

- No discontinuity at regime label flips (the system was
  classifying a 51/49 risk_on/stagflation row as risk_on yesterday
  and stagflation today; hard-switching would jump the weights by
  ~50% on a 2% probability change).
- Uses all information the classifier produces, not just the argmax.
- Hard-switching is recoverable by forcing the probability vector
  to one-hot if needed in future.
- ~10% extra compute over hard-switch; negligible.

The `/composite` page surfaces both the effective weight vector
AND the underlying regime probabilities so operators can see what
is happening when the model's behaviour seems unintuitive.

## Weight derivation: max(0, sharpe)

For each (regime, signal) bucket with `n_observations >= 20` and
non-null sharpe:

```
raw_weight = max(0.0, sharpe)
```

Then normalise per-regime so the regime row sums to 1.0, then
EWM-smooth (`alpha=0.3`) against the previous snapshot's weights.

Why max(0, sharpe) instead of |sharpe|:

- A signal with historically *negative* sharpe in a regime worked
  *backwards* in that regime. Treating that as evidence to flip
  the signal (use weight = -sharpe) would tell the system to bet
  *against* its own signal — a brittle inversion that compounds
  when the historical relationship reverts.
- Zero weight is the safer position: the signal is silenced in
  that regime, not inverted.
- Operators can still see the negative sharpe in the attribution
  heatmap and decide whether to *remove* the signal from the
  designated set, which is a hard policy decision rather than an
  auto-flip.

`weight_source` tag distinguishes provenance:

- `attribution_sharpe`: computed from real attribution data.
- `prior`: fell back to flat 1/n weights (bucket had <20 obs or
  null sharpe).
- `smoothed`: EWM blend with prior snapshot.

## Transition multiplier: explicit for linear, implicit for the
other two

`composite/transition.py:transition_multiplier(...)` returns a
multiplier in `[floor, 1.0]` based on the BOCPD changepoint
probability:

```
prob <  threshold: 1.0
prob >= threshold: 1.0 - (prob - threshold) / (1 - threshold) * (1 - floor)
prob == 1.0      : floor
```

Default: `threshold=0.5`, `floor=0.5`.

`composite.linear.v1` applies this multiplier *explicitly* in
its scoring step. `composite.bayesian_hier.v1` and
`composite.gbm.v1` *include* the changepoint probability as a
model feature (BVAR includes it as a prior centring shrink; GBM
includes it in the feature panel), so the dampening is *implicit*
in their predictions.

Rationale: linear baseline is interpretable and operators should
see the same `transition_multiplier(0.7) = 0.7` dampening they
configured. Bayesian/GBM are ML methods whose feature panels
should include the raw signal — they learn the appropriate
dampening from data, and we shouldn't double-apply it on top.

## GBM as ML method — when does it beat baselines?

Expected pattern based on Stage 4B's CausalForest comparison:

- Linear baseline wins on small samples (~500 days, ~13 instruments
  = 6500 rows). The regime panel x signal panel cross-features
  are too sparse for GBM to find non-linear interactions reliably.
- Bayesian hierarchical wins on the middle band: enough data to
  benefit from shrinkage across regimes/instruments, not enough
  for GBM to learn its 4-year window.
- GBM should win at 4+ years of multi-instrument data when the
  signal-regime interactions are genuinely non-linear (e.g. trend
  signals only work in carry_friendly regimes *and* when VIX is
  low — a 2-way interaction the linear method can't see).

The shadow status keeps GBM off the production path until we have
empirical evidence on the system's own data that it outperforms.
Phase 5 comparator's `top_5_overlap` metric is the load-bearing
gate; <70% top-5 agreement vs linear is a signal to investigate
before promoting.

## Heatmap stays 10 columns

Stage 6 next.md predicted: "Heatmap is at 10 columns; Stage 7
adds a `composite` column for 11."

This is wrong, correcting here: the /signals heatmap shows 10
signal families × 13 instruments. Each cell is a per-(family,
instrument) signal value. Composite is a different shape: 1 value
per instrument. It would *replace* the family columns, not extend
them. The /composite page (Phase 7) shows composite scores in a
ranked table; the /signals heatmap shows the raw 10-family panel.

Stage 6 next.md was written before the composite output shape was
fully nailed down. Correcting the prediction; no heatmap change
in Stage 7.

## Trend ensemble regime adjustments

Per-regime SMA-horizon weight tweaks (config-driven):

| regime              | short | medium | long |
| ------------------- | ----- | ------ | ---- |
| risk_on_growth      | 1.0   | 1.0    | 1.0  |
| risk_off_defensive  | 1.5   | 1.0    | 0.5  |
| stagflation         | 1.0   | 1.0    | 1.0  |
| carry_friendly      | 0.5   | 1.0    | 1.5  |
| vol_spike           | 1.5   | 0.75   | 0.25 |

Rationale per regime:

- `risk_off_defensive` + `vol_spike` — fast-moving markets, the
  short SMA (10-day) catches the move quickly; long SMA (200-day)
  lags badly and gets whipsawed. Down-weight long, up-weight short.
- `carry_friendly` — slow grinding markets favouring carry traders.
  The long SMA reliably identifies the regime; short SMA whipsaws
  on noise. Up-weight long, down-weight short.
- `risk_on_growth` + `stagflation` — no strong prior; keep
  Stage 3's equal weights.

These adjustments are the first signal-level consumption of regime
state, completing Stage 3's no-op `regime_state` parameter.
