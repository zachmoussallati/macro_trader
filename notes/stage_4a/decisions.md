# Stage 4A — decisions

Early decisions captured as Phase 0 / Phase 1 are built. Later phases append
sections below.

## 1. HTTP-mocking library: `respx`

- **What**: Use `respx` for the four `httpx`-based ingesters (CFTC, EIA, USDA,
  NOAA) and `monkeypatch` for the three library-wrapped ones (FRED via
  `fredapi`, yfinance via the `download` function, Google Trends via
  pytrends' `TrendReq.build_payload` + `interest_over_time`).
- **Why**: `respx` provides the cleanest httpx-native API (`@respx.mock` +
  `respx.get(url).mock(return_value=...)`); mocking at the HTTP layer is
  closer to production reality than patching internal methods. For the
  library-wrapped ingesters, however, HTTP-level mocking would require
  reverse-engineering vendor SDKs (fredapi makes multiple chained calls,
  yfinance is notorious for changing endpoints); patching the SDK's
  user-facing entry point is more stable.
- **Alternative considered**: `httpretty` (older, monkey-patches the socket
  module — too invasive). `pytest-httpx` (would work but ties us to a less
  active project). `responses` (requests-only, not httpx).

## 2. Ingester tests are unit tests with mocked DB + helpers

- **What**: Tests live in `tests/unit/data/ingestion/` and use a `MagicMock`
  session factory plus monkey-patched `create_lineage`, `finalize_lineage`,
  `record_lineage_failure`, and `touch_freshness` (the four helpers
  imported into `data.ingestion.base`). A `CallLog` dataclass captures
  arguments so tests can assert on the lineage / freshness contract
  without real DB writes.
- **Why**: Ran a first pass with real-Postgres integration tests
  (savepoint-rollback session factory). Outcome: each test took multiple
  minutes and the suite ran for hours. Diagnosis: psycopg's `rowcount`
  returns `-1` from `INSERT ... ON CONFLICT DO UPDATE` inside the
  savepoint context, breaking the ingester's `result.rowcount or
  len(chunked)` fallback; on top of that, the connection-pool churn from
  per-test outer transactions plus the alembic migration warmup made the
  feedback loop unusable. Pivoted to mock-based unit tests — they hit
  ≥90% per-ingester coverage in <2s per file and assert on the same
  contract (lineage row created/finalized; freshness touched with the
  right `success` flag).
- **What we lose**: end-to-end SQL verification per-ingester. Already
  covered by existing `tests/integration/data/test_lineage_freshness.py`
  and `test_point_in_time.py`, plus the per-table model tests run by the
  broader test suite. The persist() body's SQLAlchemy statement is
  exercised by `mock_session.execute(stmt)` so the SQL builder still
  runs; only the actual round-trip is skipped.

## 3. Shared HTTP fixtures live in `tests/fixtures/http.py`

- **What**: One module exposes builder functions for realistic provider
  responses (`fred_releases_df`, `yfinance_bars_df`, `cftc_zip_bytes`,
  `eia_series_response`, `usda_quickstats_response`, `noaa_gsom_response`,
  `pytrends_interest_df`). Each builder accepts `empty=`, `malformed=`,
  and a "shape" knob (e.g. `n_rows=`) so the five scenarios can compose
  off the same payload.
- **Why**: Per-test ad-hoc fixtures drift; shared builders keep the
  realism centralised. Each builder has a comment citing where its shape
  came from (FRED `get_series_all_releases` docs; CFTC disaggregated COT
  text file; EIA v2 API spec; etc.).

## 4. CFTC sample is generated in-memory, not a `.zip` checked into the repo

- **What**: The Stage 4A prompt suggested checking in
  `tests/fixtures/cftc_sample.zip` with a stripped-down COT weekly file.
  Instead, the `cftc_zip_bytes(...)` builder in `tests/fixtures/http.py`
  constructs the ZIP in-memory at fixture call time, parameterised by
  `report_type`, `empty=`, `malformed=`, and an optional
  `instrument_rows` map.
- **Why**: Binary fixtures are hard to inspect in PRs and tend to drift
  from real CFTC schemas without anyone noticing. The in-memory builder
  uses the actual column-header lists declared in the test fixture
  module — which themselves cite the source CFTC file they were
  copied from — so the fixture is reviewable at the diff level. Also,
  the same builder serves all five test scenarios (happy / empty /
  malformed / rate-limit / retry-exhaustion) without extra files.

## 5. Bonus: removed `format_exc_info` from the dev console processor chain

- **What**: `src/macro_trader/logging_setup.py` previously included
  `structlog.processors.format_exc_info` in the shared processor chain
  for both the JSON renderer (production) and the ConsoleRenderer (dev).
  Pivoted it into the JSON-only branch.
- **Why**: ConsoleRenderer formats exceptions itself; having
  format_exc_info upstream double-formats and emits a `UserWarning:
  Remove format_exc_info from your processor chain if you want pretty
  exceptions.` In the test suite this warning is escalated to an error
  by the project's `filterwarnings = ["error", ...]` pytest config,
  which surfaced when a malformed-payload test exercised the
  ingester's failure logger. Fix is one-line and a strict improvement
  in dev log readability too.

## 6. `respx` added to dev deps, not lockfile-pinned this session

- **What**: `respx>=0.21.0` added to `[project.optional-dependencies].dev`
  in `pyproject.toml`. `uv.lock` left out of sync because `uv` is not on
  PATH in this shell.
- **Why**: Run-time correctness only needs the pyproject entry; the
  lockfile is regenerated next time `uv sync` runs. The installed version
  (`respx==0.23.1`) is well within the floor.

## 7. Phase 1a: `reference_for` falls back to first-registered

- **What**: Added `MethodRegistry.reference_for(component)` returning the
  resolution `PRODUCTION -> BASELINE -> first-non-DEPRECATED-registered`,
  with `None` for unknown components. Distinct from `production_for`,
  which raises when no PRODUCTION/BASELINE exists.
- **Why**: The new `run_comparisons_for_component` runner needs to
  no-op cleanly when a component is partially registered (e.g.
  positioning has shadows added before its baseline lands in a separate
  commit). Raising forces every runner to wrap in try/except; returning
  `None` lets the runner skip silently with one line.
- **Excluded DEPRECATED from the fallback** so a freshly-deprecated
  method does not accidentally drive comparisons after promotion.

## 8. Phase 1a: comparator runner is module-level, not a registry method

- **What**: `run_comparisons_for_component(component, comparator, data,
  *, period_start, period_end, session=, notes="")` lives in
  `macro_trader.methods.comparator`, not on `MethodRegistry`.
- **Why**: The registry is concerned with registration + status. The
  comparator runner pulls from the registry but also drives a
  ``MethodComparator`` and persists ``ComparisonResult``. Co-locating
  it with the comparator keeps the registry surface minimal and the
  comparison-loop logic next to the abstractions it uses.

## 9. Phase 1b: dashboard "designated" resolution lives in `signals.designated`

- **What**: New module `macro_trader/signals/designated.py` exposes
  `resolve(component)` and `resolve_id(component)`. The API router
  (`api/routers/signals.py`) calls `resolve_id` per component when
  building heatmap data, replacing the hardcoded
  `DESIGNATED_PER_COMPONENT` dict.
- **Resolution order**: config override (`signals.designated_per_component`
  in `config/base.yaml`) -> registry PRODUCTION -> registry BASELINE
  -> first-registered. Documented in the module docstring.
- **Why config first**: Trend has three SMA BASELINEs plus an ensemble
  BASELINE; the registry can't tiebreak without additional metadata.
  Letting operators pin a method id via YAML is a single durable
  override that survives method renames as long as the id stays
  stable. Pointer to a missing id falls through to registry
  resolution rather than 500ing.
- **Side effect**: Added `SignalsSettings` (with
  `model_config = {"extra": "ignore"}` to tolerate the existing
  per-method yaml keys) to `macro_trader.config`. `Settings.signals`
  is now a field; YAML's `signals.designated_per_component` block is
  picked up.

## 10. Phase 1c: cross-sectional groupings use `asset_class`, not `sub_class`

- **What**: `CrossSectionalValue` no longer carries
  `DEFAULT_SUB_CLASS_GROUPS`. The hardcoded dict is removed and
  replaced by a `get_class_groups(session, instrument_ids, *, column=...)`
  query against the `market_data.instruments` table at compute time.
  Default column is **`asset_class`** (energy / base_metals /
  precious_metals / agriculture).
- **Why not `sub_class` as the Stage 4A prompt suggested**: the
  current Stage-2 seed populates `sub_class` at *commodity* granularity
  (crude_oil / natural_gas / distillate / gasoline / copper /
  aluminum / gold / silver / platinum / grains / oilseeds). Groups of
  one cannot be cross-sectionally ranked. `asset_class` is the
  coarser grouping that matches the original `DEFAULT_SUB_CLASS_GROUPS`
  exactly (a deliberately conservative choice — same semantics, just
  DB-driven). The Stage 4A prompt's expected `sub_class` values
  (`refined_products` for HO+RB, `grains` for ZC+ZS+ZW including
  soybeans) don't match the seed either; rather than reseeding mid-
  stage we expose the `class_column` constructor knob so a future
  reseed + `class_column="sub_class"` switches over cleanly.
- **Consequence**: `signals.value.cross_sectional.sub_class_groups`
  YAML block is removed; replaced with `class_column: asset_class`.
  Tests updated.

## 11. Phase 2: COT lookback 156 weeks (3 years)

- **What**: `CotZScore` and `CotCommercial` use a 156-week rolling
  window with `min_history_weeks=52` (one year minimum before any
  signal is emitted).
- **Why**: Three years balances "covers a full cycle including a
  notable extreme" against "doesn't include data so old the regime
  has changed". Twelve months as the minimum keeps the early-history
  ramp-up from emitting noise.
- **Confidence ramp**: `confidence = min(1.0, history_weeks / 156)`.
  An instrument with 30 weeks of history gets `confidence=0.19`, so
  the Stage 7 composite weighting will weight its raw positioning
  signal accordingly.

## 12. Phase 2: sign-inverted via tanh, z-score recoverable via arctanh

- **What**: `raw_value = -tanh(z.clip(-3, 3))`. The persisted
  `SignalOutput.zscore` field is `arctanh(raw_value)` so consumers can
  recover the underlying z-score (up to the clip).
- **Why**: tanh keeps values in [-1, 1] for the dashboard heatmap
  and Stage 7 composite. Storing the z-score lets the methods page
  show "this is a 2.5-sigma event" without an extra column.

## 13. Phase 2: per-instrument try/except style ≠ FRED's

- **What**: positioning `compute()` iterates instrument-by-instrument,
  calling `load_cot_as_of`. If a per-instrument loader returns an
  empty DataFrame (or all-NaN net positioning), that instrument is
  silently dropped from the output. No try/except wrapping each call
  because the loader is in-process and pure-DB; failures bubble.
- **Why**: differs from FRED-style "wrap each upstream call" because
  the upstream here is our own loader, not a flaky HTTP source. Bad
  upstream is the data-quality module's problem.

## 14. Phase 2: comparator persistence sanitizes NaN -> None

- **What**: `ComparisonResult.to_db_row()` now passes
  `metrics`/`agreement`/`stability` through `_sanitize_nans`,
  converting NaN and infinity to `None` for valid JSON.
- **Why**: Postgres JSONB rejects `NaN`. The integration test for
  positioning hit this first because empty `rolling_sharpe_252`
  averages produce NaN; same bug would have triggered on any future
  comparator with sparse data. One-line fix at the framework
  boundary is preferable to per-comparator filtering.

## 15. Phase 2: positioning Dagster asset depends on ingest_cftc_cot + daily_data_quality

- **What**: `signal_positioning` Dagster asset depends on
  `ingest_cftc_cot` (new COT data Friday) and `daily_data_quality`
  (yesterday's quality flags). Added to `compute_all_signals_job` so
  the existing 23:30 UTC schedule covers it.
- **Why**: same dependency pattern as the trend / value assets. The
  Friday COT publication arrives during the trading week; the asset
  runs daily anyway so each row's `metadata.is_fresh_data` flag can
  distinguish "this is the new weekly print" from "today repeats
  Wednesday's value".

## 16. Phase 3: PCA + DFM re-fit on every daily run, no weekly cadence yet

- **What**: Both ``PCADislocation`` and ``DynamicFactorModel`` fit on
  every ``compute()``. There is no separate refit asset; the daily
  ``signal_dislocation`` asset runs everything end-to-end.
- **Why**: PCA fit on a 13 x 252 panel is <1 second; daily refit is
  cheap. DFM fit can take 30-60s but the daily asset budget can
  absorb that on most days. Avoiding a separate refit asset keeps
  this stage's diff small while leaving the natural structure in
  place: the methods' constructors accept lookback / refit
  parameters, and ``methods/base.py`` already has
  ``serialize()`` / ``deserialize()`` hooks plus a
  ``system.methods_registry.serialized_blob`` column ready for the
  weekly cache. The follow-up is a Stage 4B/5 task tracked in
  ``tradeoffs.md``.

## 17. Phase 3: DFM convergence failures degrade gracefully

- **What**: If ``statsmodels.tsa.statespace.DynamicFactor.fit`` raises
  (convergence failure / non-stationarity / etc.), the method
  logs a warning and returns ``[]``. The daily runner persists
  whatever PCA produced and the comparator gracefully reports
  ``n_observations_b=0``.
- **Why**: DFM convergence on small synthetic samples is genuinely
  flaky in statsmodels. Crashing the entire signals job for what is
  registered as a SHADOW is the wrong trade-off: the BASELINE keeps
  driving decisions, and the missing comparison run rolls forward
  to the next day.
- **Side effect on tests**: an integration test for DFM end-to-end
  is deliberately not committed in Stage 4A; the unit-level methods
  test exercises the metadata + interface contract, and the
  comparator test covers the empty-B path. A real-data backfill in
  Stage 9 will be the first place DFM convergence is exercised
  systematically.

## 18. Phase 3: dislocation signal = -tanh(residual.clip(-3, 3))

- **What**: Both methods squash residuals through ``-tanh`` with the
  z-score clipped to [-3, 3]. Sign convention: positive residual
  (instrument outperformed peer prediction) -> contrarian short ->
  negative ``raw_value``. Confidence scales with explained variance
  (low explained_variance => weak factor structure => low confidence
  weighting downstream).
- **Why**: matches the project-wide long-bias convention (Stage 3
  trend/value) and keeps the heatmap colour scale consistent. Clip
  before tanh avoids saturation flattening interesting tail signals.

<!-- Subsequent decisions appended as Stage 4A progresses. -->
