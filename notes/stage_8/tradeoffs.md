# Stage 8 — Tradeoffs

What was deferred, why, and what it would take to revisit.

## Deferred this stage

### 1. Per-strategy portfolio sleeves

The current architecture produces *one* portfolio per method (ERC,
HRP, BL, CVaR). A more sophisticated system would have multiple
"sleeves" per method — e.g. a trend-following sleeve, a carry
sleeve, a value sleeve — each sized independently with separate
risk budgets, then aggregated.

We chose the single-portfolio approach because:

- It directly consumes the composite scores Stage 7 produces;
  composite *is* the aggregation step.
- Sleeves add an extra optimisation layer (allocate budget across
  sleeves) that needs its own attribution + tuning.
- The composite + portfolio split keeps the responsibility clean:
  composite says "what's a good opportunity"; portfolio says
  "how big to size it."

To revisit: when Stage 9's backtester shows the composite is
*averaging away* useful information (e.g. a strong trend signal
on one instrument gets diluted by weak carry on others), build a
sleeve-aware composite layer.

### 2. Transaction cost optimisation

The 4 portfolio methods minimise variance / CVaR / tracking error.
None of them include commissions, slippage, or market impact in
the objective.

We chose to defer because:

- The 13-instrument liquid-commodity universe has low transaction
  costs (~3 bps per side for futures; less for ETF proxies).
- Without realised execution data, modelling slippage as a
  function of trade size + market impact is conjectural.
- A naive `transaction_cost = const × |new_weight - old_weight|`
  penalty term is the wrong abstraction — real costs are
  non-linear in size and depend on liquidity regime.

To revisit: Stage 10+ when live execution generates realised fill
data. Add a quadratic cost term to the optimisation objective
(per Almgren-Chriss 2000) tuned on the realised data.

### 3. Multi-period optimisation

The current optimisation is myopic: solve for today's positions
given today's covariance + composite. Multi-period optimisation
would consider expected position changes over a horizon (e.g. 5
days) and incorporate the cost of rebalancing through that
horizon.

We chose myopic because:

- The 12% vol target + weekly cadence of attribution refresh
  doesn't drift fast enough for multi-period optimisation to
  meaningfully change positions.
- Multi-period needs realistic transition cost models (see #2);
  without them the lookahead is exercise futility.
- Most institutional macro CTAs run myopic optimisation; the
  rebalance cost is small enough.

To revisit: when running at higher frequency (intraday + Stage
10's execution layer) where rebalance cost becomes material.

### 4. Tail risk hedging instruments

CVaR optimises tail risk via portfolio composition but doesn't
buy *explicit* tail hedges (e.g. far-OTM puts on SPY, VIX
futures, gold). A more complete tail-risk system would add a
"hedging sleeve" that scales up during high-VIX regimes.

Deferred because:

- Stage 5's vol_surface signal family exists but it tracks IV-RV
  + skew, not hedge-instrument selection.
- The Stage 7 regime classifier's `vol_spike` regime already
  drives down trend-horizon weights (Stage 7 Phase 4) — that's
  the implicit hedge.
- Real tail-hedge instruments (puts) are options; our universe is
  futures.

To revisit: post-v1 when an options-execution layer exists.

### 5. Currency hedging

The 13-instrument universe is USD-denominated; FX is not modelled
at all. International instruments would need an FX overlay.

Deferred: out of scope for the commodities-only v1.

### 6. CVaR LP infeasibility at small n

At n=4 with `max_position_weight=0.15`, the LP equality
`sum(w) = 1` is infeasible (`4 * 0.15 = 0.60 < 1.0`). The method
catches infeasibility and falls back to equal-weight.

The right fix is to relax to `sum(w) <= 1` so the LP can run on
small universes. We chose to defer because:

- Production n=13 doesn't trigger it (`13 * 0.15 = 1.95`).
- The fallback is a defensible no-op (equal-weight inside the
  eligible set respects per-instrument cap implicitly).
- Changing the equality to inequality requires re-running all the
  unit tests against the new gross-exposure semantics.

To revisit: when the universe shrinks below 7 instruments (e.g.
emerging-market specific portfolio) where the LP would routinely
fail at the default cap.

### 7. Stationary covariance assumption

Both Ledoit-Wolf and DCC-GARCH assume covariance is *locally*
stationary over the lookback (252 / 504 days). A regime shift
during the lookback distorts the estimate; both methods react
slowly because the new regime gets diluted by stale data.

Considered: regime-conditional covariance (estimate one Sigma
per regime, blend by current probability). Deferred because:

- The Stage 6 regime probability vector is already used by the
  composite layer; double-conditioning at the covariance layer is
  expensive and may overfit.
- Stage 9 backtester is the right place to verify whether
  regime-conditional covariance materially improves OOS Sharpe.

To revisit: if Stage 9 shows regime-transition windows blow up
realised drawdown more than the gate dampens.

### 8. Bayesian posterior averaging in BL

`portfolio.black_litterman.v1` uses the He-Litterman closed-form
posterior *mean* and feeds it into mean-variance. Full posterior
averaging — draw N samples from the posterior, optimise each,
average the resulting weights — would capture more of the
uncertainty in expected returns.

Deferred because:

- 5000 samples × mean-variance solve at every daily compute is
  ~500x more expensive than the closed-form solve.
- The credible interval on the posterior mean is already
  available analytically (per Idzorek 2004) — we just don't use
  it.
- Per-row composite confidence already enters Omega; that's the
  Bayesian "view confidence" knob.

To revisit: Stage 9 if posterior-mean-only BL produces top-3
overlap < 60% with ERC and we want a smoother blend.

### 9. Per-instrument transaction-aware vol scaling

Vol-targeting scales every position by the same scalar so the
portfolio hits 12% vol. A per-instrument scaling would also
account for cost-of-trading-the-difference, but that requires
the cost model in #2.

### 10. Real-time Vitest CI / `real_data` test execution

Same operational gaps as Stages 5-7. Documented in `decisions.md`
as user responsibility per the Stage 8 prompt's Phase 0 framing.

## Deliberate non-features

### Auto-release for level-3 drawdown gate

Considered and rejected. The -10% peak drawdown is the "this
strategy is broken" threshold; auto-release would defeat the
purpose. Manual release through an authenticated API endpoint is
the only way out, deliberately inconvenient.

### Smart-sizing around an active gate

Considered: let operators bypass the gate's scaling factor on a
per-position basis (e.g. "I know this gold trade is fine, keep it
at full size"). Rejected because gates are non-negotiable; they
exist precisely to override operator judgment during stressed
periods.

### Mean-variance as a primary method

Considered making vanilla mean-variance one of the SHADOWs.
Rejected because BL is a strict superset (it *is* mean-variance
with a Bayesian prior structure). Adding plain MV would be
redundant.

### Per-method drawdown gates

Considered: each portfolio method runs its own gate so different
methods can be in different gate levels. Rejected because:

- Methods share the same upstream composite signals; if one
  method's NAV crashes, the others' would too.
- Operationally simpler to have one gate per strategy.
- Phase 9 backtester needs the gate to be consistent for fair
  comparison.

The production method's gate state applies system-wide. Each
method's positions get scaled by the *same* factor. A method
under-performing relative to another is a comparator concern,
not a gate concern.

## Open questions for Stage 9

- Walk-forward backtester needs to handle the gate state machine.
  Pre-compute realised returns; replay the gate trigger logic;
  scale positions accordingly. Stage 8's `EquityCurvePoint`
  schema is already compatible — Stage 9 just backfills it.
- Deflated Sharpe (per Bailey & Lopez de Prado 2014) requires
  knowing the number of trials. With 4 portfolio methods × 3
  composite methods × 2 covariance methods = 24 combinations,
  the deflation factor is substantial. Stage 9 should compute it.
- Monte Carlo stress should perturb the covariance matrix (e.g.
  bump correlations by +0.2 in `vol_spike`) and verify the
  portfolio + gate response is sensible.
- Position-sign stability test: across the 4 methods, what
  fraction of (instrument, day) cells have *all* methods agreeing
  on sign? Should be > 95%; below that, investigate.
