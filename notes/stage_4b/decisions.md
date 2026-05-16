# Stage 4B — decisions

## 1. Phase 0.1: lockfile resync via uv installed into the venv

- **What**: ran `uv sync --extra dev` after installing uv via the
  venv's pip. Stage 4A's `respx>=0.21.0` was added to pyproject.toml
  but the lockfile was untouched.
- **Side effect**: `uv sync` removed pip + uv themselves from the
  venv (since they aren't pyproject deps). Future re-installs go
  through `python -m ensurepip --upgrade && python -m pip install uv`.

## 2. Phase 0.2: Method state lives on `_state: dict | None`

- **What**: Both `PCADislocation` and `DynamicFactorModel` now carry a
  `_state` instance attribute. `fit_on_returns(returns)` populates it,
  `predict_on_returns(returns)` consumes it, `serialize()` pickles
  it, `deserialize(blob)` restores it on a fresh instance.
- **Why dict and not a dataclass**: the contents differ between
  methods (PCA stores a `sklearn.decomposition.PCA`; DFM stores a
  `statsmodels.tsa.statespace.dynamic_factor.DynamicFactorResults`)
  and adding a per-method dataclass would force two parallel
  definitions for very little type-safety win — pickle is the
  serialization format either way.
- **Why on the instance, not a global cache**: parallel runs (Stage
  9 backtester sliding window) need independent fitted state; a
  module-level cache would race.

## 3. Phase 0.2: `compute()` falls back to fit-on-the-fly when state is missing

- **What**: If `self._state is None` when `compute()` is called, the
  method fits on the daily window and logs
  `signals.dislocation.<method>.fallback_fit`.
- **Why**: First-ever runs have no cached state. A failed weekly
  refit (DFM convergence) should not silently zero the daily output
  for 6 days — the daily run absorbs the cost (PCA fit is sub-second;
  DFM fit is 30-60s) rather than presenting empty signals.
- **Cost**: a daily DFM fit is bigger than the weekly cadence wants,
  but it's cheaper than the alternative of "no signal until next
  Sunday".

## 4. Phase 0.2: PCA sign-alignment algorithm

- **What**: After refitting, for each new component `i` we compute
  the Pearson correlation against every prior component `j` over the
  instruments common to both fits. We pick `j*` with the maximum
  `|corr|`, and flip the sign of new component `i` if `corr[j*] < 0`.
- **Why max-abs over max-positive**: handles both sign flips AND
  reorderings between refits. A new "growth" component might have
  swapped places with the old "USD" component because their
  explained-variance ratios crossed during the week — we still want
  to align by content, not by index.
- **Note on residuals**: PCA's residual signal is sign-flip-invariant
  (flipping `components_[i]` flips `scores[:, i]` at predict time;
  the reconstruction is identical). The reason to align signs is
  for the dashboard loadings heatmap — a flipped factor displayed
  to a user looks like the world inverted. Aligning makes the
  dashboard story continuous across refits.

## 5. Phase 0.2: blob compression policy

- **What**: `_maybe_compress()` applies zlib only when raw blob >=
  32 KB; otherwise the blob is stored as-is. A `b"ZLIB"` magic
  prefix marks compressed payloads so `_maybe_decompress()` is
  unambiguous.
- **Why 32 KB threshold**: PCA state for 13 instruments x 3 components
  is well under 8 KB. DFM state pickled is typically ~50-200 KB.
  32 KB is high enough that tiny blobs don't pay zlib overhead and
  low enough that heavyweight DFM state always compresses. Postgres
  TOAST kicks in around 2-8 KB depending on column policy, so we
  rely on Postgres-side compression for small payloads and our own
  zlib for large ones.
- **Observed sizes** (from the integration test against a 5-instrument
  synthetic panel): PCA blob is ~1.5 KB. DFM is harder to test
  reliably because convergence is flaky on small samples; the path
  is exercised but the precise size will be re-measured against
  the 13-instrument production universe in Stage 5.

## 6. Phase 0.2: Dagster wiring — separate refit asset, daily depends on it

- **What**: New asset `dislocation_models_refit` (group
  `signals_dislocation`, schedule `dislocation_refit_weekly_sunday_0000_utc`).
  The daily `signal_dislocation` asset gains
  `dislocation_models_refit` as an `AssetIn` so Dagster's lineage
  is correct: the daily run waits for the refit if both are
  scheduled to materialise in the same window.
- **Why a separate job for the schedule**: keeps the weekly cadence
  controllable independently from the daily; an operator can
  trigger an out-of-band refit (after universe expansion, after a
  bad week) without re-running every signal family.

## 7. Phase 0.3: sub-class reseed groups

- **What**: Updated the per-instrument `sub_class` values to coarse,
  rankable groups:
  - `crude_oil`: CL, BZ
  - `refined_products`: NG, HO, RB (NG bundled here per the prompt
    so we don't end up with a singleton group; same liquidity pillar)
  - `base_metals`: HG, ALI
  - `precious_metals`: GC, SI, PL
  - `grains`: ZC, ZS, ZW
- **Why bundle NG with refined_products**: Stage 4A's seed had NG as
  its own `natural_gas` sub_class (singleton => unrankable). The
  prompt suggests grouping NG with refined products even though it's
  technically a separate commodity, because cross-sectional value
  needs at least a pair to rank against. Within the energy
  asset_class, refined_products is the closest peer set.
- **`CrossSectionalValue` default flipped to `class_column="sub_class"`**:
  the new sub_class column is now the canonical fine-grained grouping.
  `class_column="asset_class"` stays available for callers that want
  the coarser energy / base_metals / etc.

## 8. Phase 0.3: integration test verifies "moving an instrument changes its rank"

- **What**: `test_changing_instrument_sub_class_changes_rank` upserts
  NG into `crude_oil` mid-run and re-runs `CrossSectionalValue`. The
  test asserts NG's rank actually changes — proving the resolver
  reads the live row, not a cached / hardcoded value.
- **Why**: belt-and-braces for Phase 1c's "DB-driven groupings"
  promise. If someone re-introduces a hardcoded fallback dict, this
  test breaks.

## 9. Phase 1: EconML behind an optional `[ml]` extra (Option B)

- **What**: `econml>=0.15.0` lives in
  `[project.optional-dependencies].ml` rather than `dev`. Install
  via `uv sync --extra ml` to pick up the Causal Forest method.
- **Why Option B over Option A**: EconML pulls ~200 MB of transitive
  ML deps. Production deploys (and most local dev shells) don't need
  causal inference — they only need OLS + RF + the rest of the
  signal stack. Putting EconML behind an extra keeps the default
  install lean and forces operators to opt-in when they actually
  want causal-method outputs.
- **Graceful degradation**: `_econml_available()` is checked at
  three places — `register.py` (logs `methods.setup.skipped` and
  doesn't register the CF method), `runner.py` (CF is omitted from
  `default_factor_exposure_methods`), and `refit.py` (CF refit is
  skipped silently). The `CausalForestFactorExposure` constructor
  itself raises `RuntimeError` immediately if EconML is missing —
  loud and obvious if someone instantiates it by hand without the
  extra.

## 10. Phase 1: factor selection — six factors from FRED

- **growth**     INDPRO yoy_change
- **inflation**  CPIAUCSL yoy_change
- **liquidity**  DFII2 level_inverted (lower real yields = more
                 liquidity = positive factor)
- **usd**        DTWEXBGS 60d_return (broad trade-weighted USD)
- **oil**        DCOILWTICO 60d_return
- **risk_on**    VIXCLS 60d_change_inverted (lower VIX change =
                 risk-on)

Two new FRED series added to `FRED_SERIES`: `DFII2`, `VIXCLS`.
The other four were already ingested in Stage 2.

## 11. Phase 1: OLS as baseline because it's the cleanest contract

- **What**: `factor_exposure.ols.v1` (BASELINE), `.rf.v1` (SHADOW),
  `.causal_forest.v1` (SHADOW gated on EconML).
- **Why OLS as baseline**: closed-form, fast, R^2 is a clean
  confidence score, betas are interpretable to a portfolio manager.
  The shadows have to clear a pre-declared bar (sharpe / max_dd /
  stability uplift) before promotion — not a vague "more
  sophisticated" argument.

## 12. Phase 1: composite score = -beta @ z_today

- **What**: today's signal is the negation of the dot product of
  fitted betas (or RF prediction / CATE for the shadows) with
  today's factor z-scores. Squashed through tanh to bound in
  [-1, 1]. ``zscore`` field stores the pre-tanh raw score.
- **Sign convention**: positive raw_value = long bias. A positive
  factor reading + positive exposure means the instrument is "ahead
  of" the factor → contrarian short tilt → negative pre-tanh score
  → tanh → negative raw_value. Matches the project-wide convention
  established in Stage 3.

## 13. Phase 1: refit cadence staggered Sunday 01:00 UTC

- **What**: dislocation refit at Sunday 00:00 UTC (Stage 4B Phase
  0.2); factor exposure refit at Sunday 01:00 UTC; catalyst refit
  reserved for Sunday 02:00 UTC (Stage 4B Phase 2). One hour
  between stages so a runaway refit on one family doesn't block the
  next.
- **Daily inference at 23:30 UTC** still runs all five (eventually
  seven) signal families against whatever cached state is current.

## 14. Phase 1: per-instrument fit, NOT per-instrument-row drop

- **What**: `fit_on_panels` uses `inner-join + dropna(how=any)` to
  align returns and factor panels. If one instrument has too many
  NaN rows, the fit window shrinks for everyone — the entire fit
  may fail with `_state = None` rather than dropping just that one
  instrument.
- **Why this trade-off**: simplifies the implementation; per-
  instrument NaN handling needs a per-instrument loop with its own
  alignment which adds complexity. The integration test
  `test_ols_returns_no_state_when_inner_join_drops_too_many_rows`
  pins the current behaviour. A per-instrument-skip refactor is
  tracked in `tradeoffs.md`.

## 15. Phase 2: catalyst event-study sensitivity = mean(|return|) - baseline_vol

- **What**: Per (instrument, event_subject) pair, sensitivity =
  ``mean(|log_return_in_window|) - baseline_vol`` with default
  window [-1, +1] days around the event.
- **Why subtract baseline_vol**: bare mean(|return|) overstates
  catalyst impact when an instrument is just generally volatile.
  Subtracting recent baseline vol (60-day std of log-returns) gives
  the *abnormal* portion — closer to the canonical event-study
  abnormal-return contract.
- **Min events for estimate = 5**: subjects with fewer historical
  instances are dropped silently. Five is a low bar but high enough
  that one lucky reading can't dominate; the dashboard exposes
  ``n_subjects`` per instrument so coverage gaps are visible.

## 16. Phase 2: linear time-decay over a 10-day forward window

- **What**: each upcoming event contributes
  ``sensitivity * max(0, 1 - days_to_event / forward_window_days)``
  to its instruments' forward score. Default window = 10 days.
- **Why linear over exponential**: linear is more interpretable and
  matches how a portfolio manager naturally weights "an FOMC three
  days out" vs "in nine days". Exponential is selectable
  (``decay="exponential"``); ten-day half-life is configured but
  not the default.
- **Sign convention**: forward score's *sign* is currently zero by
  default (we don't know whether the next CPI will surprise high or
  low). Magnitude carries the catalyst risk. Stage 6 (regime
  classifier) will add directional priors via expected reaction by
  regime.

## 17. Phase 2: causal method is a Stage-4C placeholder

- **What**: ``CausalCatalyst`` is registered as SHADOW when EconML
  is installed, but its ``compute()`` currently delegates to
  ``EventStudyCatalyst`` and re-tags ``method_id`` /
  ``placeholder_for_cate=True`` in the metadata blob.
- **Why ship the placeholder**: end-to-end wiring is testable now
  (registry, refit blob, runner, Dagster asset, comparator pair).
  The CATE estimation itself needs more careful work on synthetic-
  data convergence and proper double-machine-learning splits — best
  done against real backfills in Stage 4C / 9, not against Stage 4B's
  thin synthetic test fixtures.
- **What this means for the dashboard**: until Stage 4C, the two
  catalyst methods produce identical signals. The methods page shows
  both, but the comparator's value_correlation will be 1.0 — the
  conventional indicator that the shadow hasn't differentiated yet.

## 18. Phase 2: refit cadence Sunday 02:00 UTC

- **What**: ``catalyst_models_refit`` runs Sunday 02:00 UTC,
  staggered after dislocation (00:00) and factor exposure (01:00).
- **Why**: same staggering rationale as Phase 1 (a runaway refit
  shouldn't block downstream weekly assets). The historical-event
  load + sensitivity computation across the trailing 5 years is
  fast (<10 seconds for the current event volume) but will grow
  with calendar coverage in Stage 5+.

<!-- Subsequent decisions appended as Stage 4B progresses. -->
