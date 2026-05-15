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

<!-- Subsequent decisions appended as Stage 4B progresses. -->
