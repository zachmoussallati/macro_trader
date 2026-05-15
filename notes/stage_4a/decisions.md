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

<!-- Subsequent decisions appended as Stage 4A progresses. -->
