# Data proxies and eventual targets

This system is built around **futures markets** but for Stage 2 we use
**ETF proxies** because they're free, well-documented, and good enough to
develop signals on. Every proxy has a known tracking error — document
it here so signals can compensate or avoid the worst regimes.

The full list of paid-data targets (Norgate, CSI, Refinitiv, etc.) is
captured per row; Stage 12+ will swap these in when execution comes online.

## Current universe (13 commodities)

| Instrument | Name | Asset class | ETF proxy | Eventual target | Tracking notes |
| --- | --- | --- | --- | --- | --- |
| CL  | WTI Crude Oil      | energy           | USO  | NYMEX:CL1!  | Significant contango bleed; do not use for short-term return signals without roll adjustment. |
| BZ  | Brent Crude Oil    | energy           | BNO  | ICE:BZ1!    | Tracks Brent via futures; similar contango drag. |
| NG  | Natural Gas        | energy           | UNG  | NYMEX:NG1!  | Historically the worst-tracking ETF in the universe; use only for signals robust to large basis drift. |
| HO  | Heating Oil        | energy           | UHN  | NYMEX:HO1!  | Low volume; verify liquidity before relying on intraday prints. |
| RB  | RBOB Gasoline      | energy           | UGA  | NYMEX:RB1!  | Modest tracking error from rebalances. |
| HG  | Copper             | base_metals      | CPER | COMEX:HG1!  | Mild contango drag. |
| ALI | Aluminum           | base_metals      | JJU  | LME:AH1!    | JJU is an ETN with credit risk; LME alu lacks a clean ETF proxy. |
| GC  | Gold               | precious_metals  | GLD  | COMEX:GC1!  | Cleanest precious-metals proxy in the universe (physical-backed, tight tracking). |
| SI  | Silver             | precious_metals  | SLV  | COMEX:SI1!  | Physical-backed; spreads widen during stress. |
| PL  | Platinum           | precious_metals  | PPLT | NYMEX:PL1!  | Physical-backed; low ADV vs gold / silver ETFs. |
| ZC  | Corn               | agriculture      | CORN | CBOT:ZC1!   | Roll yield drag; switch to a roll-adjusted series for backtests. |
| ZS  | Soybeans           | agriculture      | SOYB | CBOT:ZS1!   | Similar contango drag to CORN. |
| ZW  | Wheat (CBOT)       | agriculture      | WEAT | CBOT:ZW1!   | Tracks CBOT (soft red), not KC HRW; structural backwardation rare. |

Seeded by `src/macro_trader/data/seed/instruments_seed.py`; the `instrument_id` is
the stable internal symbol that the rest of the codebase joins on.

## Future asset classes (not seeded yet)

When we add these in Stage 4+ they'll get the same `(instrument_id, name,
asset_class, proxy_ticker, underlying_ref, tracking_notes)` shape in
`market_data.instruments`. The current `instruments.asset_class` enum
already accepts these values.

### FX
G10 majors: EUR, JPY, GBP, AUD, CAD, CHF, NZD, NOK, SEK.
EM: BRL, MXN, ZAR, TRY, INR.

- **Free**: Yahoo FX (via yfinance) for spot rates, FRED for trade-weighted
  indices.
- **Paid eventual**: Bloomberg / Refinitiv (spot + forwards + options).
- **Watch**: turn-of-day timing — Yahoo's "close" is loose. For backtests
  use 17:00 NY rates consistently.

### Rates
US 2y / 5y / 10y / 30y, UK gilts (2y/10y), Bunds (2y/10y), JGBs (10y).

- **Free**: FRED for US (`DGS2`, `DGS10`, etc.), ECB / BoE / BoJ stat sites
  for non-US. **Stage 2 already pulls the US tenors.**
- **Paid eventual**: Refinitiv yield curves.
- **Watch**: holiday calendars differ by exchange; align via
  `pandas_market_calendars`.

### Equity indices
ES (S&P 500), NQ (Nasdaq 100), RTY (Russell 2000), FESX (Euro Stoxx 50),
NKD (Nikkei 225).

- **Free**: ETF proxies SPY, QQQ, IWM, FEZ, EWJ via yfinance.
- **Paid eventual**: futures vendor (CME / Eurex) with first-notice-day
  roll handling.
- **Watch**: SPY / QQQ have dividend yield gaps to futures; use total-return
  ETFs (SPLG, etc.) for return signals.

### Credit
CDX IG / HY, iTraxx Main / Crossover.

- **Free**: limited (ETF proxies LQD for IG, HYG for HY are noisy; credit
  default swap spreads aren't free).
- **Paid eventual**: Markit / IHS for series-level CDS spreads.
- **Watch**: ETF proxies for credit are the noisiest in the universe; treat
  signals built on them with extreme skepticism until paid data arrives.
