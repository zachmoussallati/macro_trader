# Data sources

Per-source notes: auth, rate limits, quirks, observed quality issues.
This is the operational reference — for the registry of what's tracked
see `src/macro_trader/data/seed/sources_seed.py`.

---

## FRED + ALFRED

- **What we use**: `fredapi` Python client.
- **Auth**: free API key from <https://fred.stlouisfed.org/docs/api/api_key.html>.
  Set in `.env` as `FRED_API_KEY`.
- **Rate limit**: ~120 req/min soft. Stage 2 hits well under this.
- **Vintage handling**: we always call `get_series_all_releases` so
  every revision is preserved. `realtime_start` / `realtime_end` map
  directly to the FRED ALFRED concept. The latest vintage has
  `realtime_end = NULL`.
- **Quirks**:
  - Some series return `'.'` for missing values; we coerce to `None`.
  - Holiday dates: rare values land on weekends due to FRED's averaging
    quirks. Idempotency on `(series_id, value_ts, observation_ts)` handles
    overwrites cleanly.
- **Schedule**: daily at 13:00 UTC (after US morning macro releases).

## yfinance

- **What we use**: `yfinance` Python client.
- **Auth**: none (unofficial).
- **Rate limit**: undocumented; back off aggressively on 429. We use
  tenacity with exponential backoff up to 30s.
- **Quirks**:
  - Newer yfinance returns a multi-index column structure for single
    tickers; the ingester flattens it.
  - "Adj Close" vs "Close": both stored; signals should pick.
  - Sometimes returns NaN-only rows for the most-recent trading day if
    fetched before market close — these are filtered out.
- **Schedule**: daily at 22:00 UTC (after US close).

## CFTC COT

- **What we use**: weekly CSV download from cftc.gov (no API).
- **Auth**: none.
- **Rate limit**: none documented; we download once per week.
- **Format**: a ZIP containing a single TXT file with comma-separated
  values and a header row that's stable year-over-year.
- **Quirks**:
  - CFTC code mapping needs quarterly review — Brent's code in particular
    has rotated.
  - Reports cover Tuesday-of-week positions; published Fridays ~15:30 ET.
  - We currently load only the disaggregated futures-only file. Legacy +
    TFF can be wired similarly.
- **Schedule**: weekly Friday at 19:00 UTC (post-release).

## EIA

- **What we use**: EIA Open Data v2 REST API.
- **Auth**: free key from <https://www.eia.gov/opendata/register.php>.
  Set `EIA_API_KEY` in `.env`.
- **Rate limit**: 5000 req/hour. We use << 100.
- **Quirks**:
  - The petroleum weekly report drops Wednesdays 10:30 ET, natural gas
    Thursdays 10:30 ET. We refresh both via the same job daily; the
    `value_ts` reflects the report date.
  - Holidays push releases by one day; idempotency handles re-fetches
    after corrections.
- **Schedule**: daily at 15:00 UTC (covers both petroleum + nat gas refresh).

## USDA NASS Quick Stats

- **What we use**: the QuickStats REST API.
- **Auth**: free key from <https://quickstats.nass.usda.gov/api>. Set
  `USDA_API_KEY` in `.env`.
- **Rate limit**: no published limit; be polite.
- **Stage 2 scope**: production + yield for corn / soybeans / wheat at
  the national, annual level. Crop Progress (weekly during growing
  season) and Grain Stocks (quarterly) are scaffolded but not yet
  wired into the asset.
- **Quirks**:
  - Value column is sometimes `'(D)'` (disclosure-suppressed); coerced to
    `None`.
  - Quarterly reports come with their own filing dates — we use the
    record's `year` as the value_ts (12-31 of that year).
- **Schedule**: monthly cadence (rolls naturally as data refreshes; the
  alt-data daily job catches new rows).

## NOAA Climate Data Online

- **What we use**: NOAA CDO v2 REST API.
- **Auth**: free token from <https://www.ncdc.noaa.gov/cdo-web/token>.
  Set `NOAA_API_KEY` in `.env` (it's a token but we use the same
  variable name).
- **Rate limit**: 5 req/sec, 10000 req/day.
- **Stage 2 scope**: US-aggregate HDD / CDD via the GSOM (Global Summary
  of the Month) dataset. Drought Monitor is on the calendar but not yet
  ingested.
- **Stage 3 expansion**: regional FIPS aggregates added for the US corn
  belt (Iowa `FIPS:19`, Illinois `FIPS:17`) and Kansas winter wheat
  (`FIPS:20`). South American coverage (Brazilian Mato Grosso for soy,
  Pampas region in Argentina) requires non-NOAA providers and is deferred
  to Stage 4 ag signals.
- **Quirks**:
  - GSOM lags by ~5 days vs end of month; expect freshness to look one
    cycle behind.
  - Station-level data is voluminous; we deliberately use country / FIPS
    aggregates.

## Google Trends

- **What we use**: `pytrends` (unofficial).
- **Auth**: none.
- **Rate limit**: very aggressive 429s. We wait 2s between queries and
  5s on errors. Backfilling >1 year of queries usually fails.
- **Stage 2 queries**: `recession`, `inflation`, `oil price`,
  `gas prices`, `buy gold`, `copper price`. Configurable via `config/base.yaml`.
- **Quirks**:
  - Returned scale is 0–100, normalised within the query window. Two
    Trends downloads for the same term run weeks apart are NOT directly
    comparable in absolute terms.
  - Some terms periodically return empty — code falls through gracefully.

---

## Observed Stage 2 quality issues

> Empty list until first daily quality run. Update this section after the
> first week of data — entries should record `series_id`, the issue, and
> what we did about it.
