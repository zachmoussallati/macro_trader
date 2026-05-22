# Stage 8 — Usage

How to run portfolio construction, interpret positions, operate the
drawdown gate.

## Running portfolio construction locally

```bash
# 1. Ensure Stage 7 composite scoring has populated.
uv run dagster job execute -f orchestration/definitions.py \
  -j composite_score_job

# 2. Daily covariance estimates (Ledoit-Wolf + DCC-GARCH).
uv run dagster job execute -f orchestration/definitions.py \
  -j covariance_estimates_job

# 3. (Optional, weekly) DCC-GARCH refit.
uv run dagster job execute -f orchestration/definitions.py \
  -j covariance_dcc_refit_job

# 4. Daily portfolio sizing across all 4 methods.
uv run dagster job execute -f orchestration/definitions.py \
  -j portfolio_positions_job
```

In production Dagster runs these on the cron schedule (composite
23:45 UTC → covariance 23:50 UTC → portfolio 00:05 UTC next day,
weekly DCC refit Sunday 07:30 UTC).

## Querying positions + risk

```bash
# Today's sized positions for the production method (ERC).
curl http://localhost:8000/api/v1/portfolio/positions

# Bayesian's positions (shadow comparison).
curl 'http://localhost:8000/api/v1/portfolio/positions?method_id=portfolio.black_litterman.v1'

# Per-instrument vol + block decomposition.
curl http://localhost:8000/api/v1/portfolio/risk

# Equity curve since inception.
curl http://localhost:8000/api/v1/portfolio/equity

# Position history for one instrument.
curl 'http://localhost:8000/api/v1/portfolio/positions/history?instrument=CL'

# Current drawdown state.
curl http://localhost:8000/api/v1/portfolio/drawdown

# Correlation matrix snapshot.
curl http://localhost:8000/api/v1/portfolio/covariance
```

The `/portfolio` dashboard page wraps these into a single UI.

## Interpreting positions

Each position has:

- `target_weight` — post-gate; what the system would actually
  trade. Bounded by `max_position_weight` × `target_vol_scaling`.
- `pre_gate_weight` — what the optimiser produced *before* the
  drawdown gate's scaling factor applied. The pair lets you
  audit the gate's effect.
- `expected_vol_contribution` — fraction of total portfolio
  variance contributed by this instrument. ERC targets 1/n per
  instrument; HRP varies with cluster size; BL/CVaR vary with
  expected returns.
- `composite_score` — the underlying composite that drove the
  position direction. |score| < 0.10 means the position would be
  dropped from the optimisation entirely.
- `block` — asset class membership for the per-block constraint.
- `gate_level` / `gate_scaling_factor` — current drawdown gate
  state at the time the position was persisted.

Sign convention: `sign(target_weight) == sign(composite_score)`
always. Magnitude depends on the portfolio method.

## When to switch designated portfolio method

Default: `portfolio.erc.v1` is BASELINE → designated as production
via `signals.designated_per_component.portfolio_construction`.

To promote a shadow to production:

```yaml
# config/<env>.yaml
signals:
  designated_per_component:
    portfolio_construction: portfolio.hrp.v1
```

What to check before promoting (per the
`PortfolioConstructionComparator` metrics):

- `position_sign_agreement >= 0.95` against the current production
  method. Below 95% means the candidate is making materially
  different *directional* calls — investigate.
- `top_3_long_overlap >= 0.60` and `top_3_short_overlap >= 0.60`.
  These are the headline-trade alignment metrics.
- `weight_correlation >= 0.70`. Lower indicates one method is
  concentrating where the other is diversifying.
- `expected_vol_a / expected_vol_b ≈ 1.0` (within 10%). Both
  methods should hit the same vol target.

## Operating the drawdown gate

The gate is the safety brake. It triggers automatically; auto-
releases for levels 1+2; requires manual release for level 3.

### Level 1 (-5% daily)

Triggered when a single trading day's portfolio return drops 5%
or more. Positions scale to 0.5x for 3 trading days, then
auto-release. Operators should expect this to fire a few times a
year in stressed markets.

### Level 2 (-8% trailing 5-day)

Triggered when the trailing 5-day cumulative return drops below
-8%. Positions scale to 0.3x for 10 trading days, then
auto-release. Compounds with level 1 if both active (factor
0.5 * 0.3 = 0.15x).

Operators should investigate when this fires: it usually means
the composite scoring is wrong on the regime, the covariance
estimate is stale, or the system has hit a genuine drawdown.

### Level 3 (-10% peak-to-trough)

Triggered when NAV drops 10% from its running peak. Positions
go to **zero** until manually released.

**Manual release procedure:**

1. Investigate the cause. Check:
   - `/composite` — is the composite signal genuinely wrong?
   - `/regime` — is the regime classifier mid-transition?
   - `/portfolio/covariance` — is the covariance matrix stale or
     ill-conditioned?
   - `/portfolio/equity` — what was the path to -10%?
2. Decide whether the strategy is still operating per spec or
   whether to halt entirely.
3. If proceeding, release via the API:

```bash
curl -X POST \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  'http://localhost:8000/api/v1/portfolio/drawdown/release?method_id=portfolio.erc.v1'
```

Or via the dashboard's manual release button on `/portfolio`
(visible only when L3 is active; requires admin login).

The API endpoint is admin-auth-gated by design. The dashboard
hides the button when L3 is not active. The release is logged in
the `system.heartbeat` table with a `portfolio.drawdown` source
tag for audit.

After release: the gate goes back to "none", positions resume at
full size on the next portfolio run. The system does NOT
auto-trigger L3 again unless a new -10% peak drawdown occurs.

## Regenerating positions manually

```python
from macro_trader.portfolio.construction.runner import run_daily_portfolio
from macro_trader.db.engine import get_session

with get_session() as session:
    written = run_daily_portfolio(session)
    print(written)  # {'portfolio.erc.v1': 13, 'portfolio.hrp.v1': 13, ...}
    session.commit()
```

This bypasses the Dagster scheduler but uses the same code path.
Useful for manual backfills.

## Inspecting the equity curve

```python
from datetime import datetime, timedelta
import requests

r = requests.get(
    "http://localhost:8000/api/v1/portfolio/equity",
    params={"method_id": "portfolio.erc.v1"},
)
for p in r.json()[-10:]:
    dd = (p["drawdown_from_peak"] or 0) * 100
    print(
        f"{p['as_of'][:10]}  "
        f"nav={p['nav']:.4f}  "
        f"daily={(p['daily_return'] or 0) * 100:+.2f}%  "
        f"dd={dd:+.2f}%"
    )
```

## Inspecting block exposure

```bash
curl http://localhost:8000/api/v1/portfolio/risk | jq '.block_exposure'
```

Each block share is gross-exposure-normalised. The 40% cap is
verified — values above 0.40 indicate a constraint failure to
investigate.

## Troubleshooting

### "No positions yet" on /portfolio

Run `portfolio_positions_job` (00:05 UTC next day). Requires
`composite_score_job` + `covariance_estimates_job` to have populated
first.

### Equity curve is empty

The first daily run starts a fresh equity curve at NAV 1.0. Wait
for the next portfolio run to populate the first row (no realised
return for day 0).

### "No covariance" error in logs

`covariance_estimates_job` hasn't run, or the Ledoit-Wolf fit
failed (typically because the trailing 252-day return panel is
empty). Check `portfolio.covariance_estimates` for recent rows.

### Drawdown release endpoint returns 409

Level 3 is not currently active. The release endpoint is only
relevant for L3; levels 1 + 2 auto-release.

### Portfolio vol stays at exactly 0.12 always

Vol-targeting is operating correctly. Each method scales its
output to hit the configured target after the optimiser solve.

### Position sign mismatches composite sign

This shouldn't happen — every method enforces sign overlay after
the optimiser. If you see this, the composite sign was missing
(score very close to zero); the method dropped it from the
eligible set.
