# Stage 5 — usage

## Running the new signal families

```bash
# Daily: all 10 families via the master job
python make.py dagster   # or via UI: jobs -> compute_all_signals_job
```

Each new family also has its own runner if you want to invoke one
in isolation:

```python
from macro_trader.db.engine import get_sessionmaker
from macro_trader.signals.alt_data.runner import run_daily_alt_data
from macro_trader.signals.nowcasting.runner import run_daily_nowcasting
from macro_trader.signals.vol_surface.runner import run_daily_vol_surface

sf = get_sessionmaker()
with sf() as s:
    run_daily_alt_data(s)
    run_daily_nowcasting(s)
    run_daily_vol_surface(s)
    s.commit()
```

## Refitting nowcasting models manually

```bash
# Via Dagster UI: jobs -> nowcasting_refit_job
# Or via Python:
from macro_trader.signals.nowcasting.refit import run_weekly_refit
with sf() as s:
    print(run_weekly_refit(s))
    s.commit()
```

Returns a list of `NowcastingRefitResult` with `blob_size_bytes`,
`n_releases_fitted`, `compressed`. Same shape as the dislocation
+ factor_exposure + catalyst refit results.

## Vol surface: ingesting options chains

The options-chain ingester isn't yet wired to a Dagster asset
(intentional — vol_surface signals are stateless and the chain
table is only populated when the ingester runs explicitly). To
seed the chains table for testing:

```python
from datetime import datetime, timezone
from macro_trader.db.engine import get_sessionmaker
from macro_trader.signals.vol_surface.ingestion.yfinance import (
    YfinanceOptionsIngester,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from macro_trader.db.models.market_data import OptionsChain

ingester = YfinanceOptionsIngester()
sf = get_sessionmaker()
with sf() as s:
    for inst, ticker in [("GC", "GLD"), ("SI", "SLV"), ("CL", "USO")]:
        rows = ingester.fetch_chain(
            inst, proxy_ticker=ticker, now=datetime.now(timezone.utc)
        )
        if rows:
            stmt = pg_insert(OptionsChain).values(
                [
                    {
                        "instrument_id": r.instrument_id,
                        "snapshot_ts": r.snapshot_ts,
                        "expiry_ts": r.expiry_ts,
                        "strike": r.strike,
                        "option_type": r.option_type,
                        "bid": r.bid, "ask": r.ask, "last": r.last,
                        "volume": r.volume, "open_interest": r.open_interest,
                        "implied_vol": r.implied_vol,
                        "delta": r.delta, "gamma": r.gamma,
                        "vega": r.vega, "theta": r.theta,
                        "underlying_price": r.underlying_price,
                        "source": r.source,
                    }
                    for r in rows
                ]
            )
            s.execute(stmt.on_conflict_do_nothing())
    s.commit()
```

Stage 6 will wire this into a proper Dagster asset
(`ingest_options_chains`) once vol surface signals start producing
meaningful output to act on.

## Swapping yfinance for a paid options source (post-v1)

1. Add a new file `signals/vol_surface/ingestion/<provider>.py`
   subclassing `OptionsChainIngester`. Implement `fetch_chain()`.
2. Update the manual ingest snippet above (or the eventual Dagster
   asset) to instantiate your new ingester.
3. Update `config/base.yaml` `signals.vol_surface.data_source` for
   documentation; the methods themselves are source-agnostic
   (they read from `market_data.options_chains` regardless of who
   wrote the rows).
4. When historical depth is available, remove the
   `historical_backtest_supported: False` flag from the methods'
   metadata. Stage 9 backtester will then include the family.

## Interpreting nowcasting outputs

Each daily signal output for a nowcasting method carries:

- `raw_value`: tanh-squashed standardised surprise.
- `zscore`: the pre-tanh surprise standardised by predictive
  std (BVAR) or a fallback denominator (OLS).
- `confidence`: bounded by R² (OLS) or posterior variance (BVAR)
  + days-since-last-release.
- `metadata.releases`: list of release_ids that contributed to
  the instrument's score.

```bash
# Top-pressure instruments by nowcasting surprise:
curl 'http://localhost:8000/api/v1/signals/values?as_of=...' | \
  jq '[.[] | select(.signal_id == "nowcasting.ols_ar.v1")] | sort_by(.zscore | abs) | reverse | .[0:5]'
```

## Interpreting vol surface outputs

Pay attention to:

- `metadata.chain_freshness_hours`: how stale the cached chain is.
  Stage 5's loader expects snapshots within 48h.
- `metadata.n_strikes_used`, `metadata.n_expiries_used`: low values
  indicate thin coverage (UNG, DBA often have <20 strikes total).
- `metadata.calendar_arbitrage_violations` (SVI method only): list
  of `{expiry_dte, n_violations}` dicts.
- `metadata.historical_backtest_supported`: always `False` in
  Stage 5; Stage 9 backtester reads this and skips the family.

## Interpreting alt-data outputs

Each method covers a different sub-universe; the `metadata.covered`
field is `True` only for instruments the method actually targets.
Uncovered instruments get `raw_value=0`, `confidence=0` so they
don't pollute composite scoring.

```bash
# EIA-storage-driven signals (only CL/BZ/NG/HO/RB are covered):
curl 'http://localhost:8000/api/v1/signals/alt_data.eia_storage.v1/values' | \
  jq '.[] | select(.confidence > 0) | {instrument_id, raw_value, confidence}'
```
