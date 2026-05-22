# Stage 8 — Methods Registry additions

6 new methods join `system.methods_registry`:

- 2 under `covariance_estimate`
- 4 under `portfolio_construction`

## Covariance methods

### `covariance.ledoit_wolf.v1` — BASELINE

| Field        | Value                                                         |
| ------------ | ------------------------------------------------------------- |
| method_id    | `covariance.ledoit_wolf.v1`                                   |
| component    | `covariance_estimate`                                         |
| version      | `1.0.0`                                                       |
| status       | `BASELINE` (designated as production)                         |
| refit        | stateless; recomputed daily                                   |
| dependencies | numpy, pandas, sklearn                                        |

**What it does.** Sample covariance over the trailing 252-day
return panel, shrunk toward a structured target (sklearn's
default constant-correlation target). The shrinkage intensity is
the Ledoit-Wolf closed-form optimal value.

**Parameters** (config `portfolio.covariance.ledoit_wolf`):

- `lookback_days: 252`
- `min_observations: 60` (skip the entire run with fewer rows)
- `shrinkage_target: "constant_correlation"` (sklearn default)

**Output metadata.** `shrinkage` (the optimal LW intensity that
was applied), `n_observations` (rows used). The full covariance
matrix is annualised by 252 trading days before persistence.

**Strengths.** Stable in small samples; no hyperparameter tuning;
guaranteed PSD; institutional standard.

**Weaknesses.** Stationary-in-window assumption: a regime shift
mid-lookback distorts the estimate. Equal-weight in time (no
recency bias).

### `covariance.dcc_garch.v1` — SHADOW

| Field        | Value                                                         |
| ------------ | ------------------------------------------------------------- |
| method_id    | `covariance.dcc_garch.v1`                                     |
| component    | `covariance_estimate`                                         |
| version      | `1.0.0`                                                       |
| status       | `SHADOW`                                                      |
| refit        | weekly Sunday 07:30 UTC                                       |
| dependencies | numpy, pandas, **arch**                                       |

**What it does.** Per-instrument GARCH(1, 1) volatilities + DCC
(Dynamic Conditional Correlation) across pairs:

- Stage 1: fit a GARCH(1, 1) per instrument; collect conditional
  volatilities + standardised residuals.
- Stage 2: feed the standardised-residual matrix into Engle's DCC
  recursion. Q_t = (1 - alpha - beta) × Q_bar + alpha × eps_t ×
  eps_t^T + beta × Q_{t-1}.
- Stage 3: correlation R_t = diag(Q_t)^{-1/2} × Q_t ×
  diag(Q_t)^{-1/2}; covariance = D × R × D.

**Parameters** (config `portfolio.covariance.dcc_garch`):

- `lookback_days: 504`
- `min_observations: 120`
- `garch_p: 1`, `garch_q: 1`
- `dcc_alpha_init: 0.05`, `dcc_beta_init: 0.93`

**Output metadata.** `alpha` / `beta` (the DCC parameters used),
`n_observations`. The fitted state (per-instrument GARCH params,
Q_bar, latest Q_t, latest conditional vols) is serialised via the
standard `store_serialized_blob` pattern (~50 KB pickled, zlib
compressed when > 32 KB).

**Strengths.** Captures regime-dependent correlation structure
that LW misses. Correlations spike during crisis periods; DCC
adapts within days while LW takes weeks.

**Weaknesses.** ~5x heavier to fit than LW. Univariate GARCH fits
can fail on flat or extreme series; the implementation falls
back to sample stdev for failed instruments with a warning logged.

## Portfolio construction methods

### `portfolio.erc.v1` — BASELINE

| Field        | Value                                                         |
| ------------ | ------------------------------------------------------------- |
| method_id    | `portfolio.erc.v1`                                            |
| component    | `portfolio_construction`                                      |
| version      | `1.0.0`                                                       |
| status       | `BASELINE` (designated as production)                         |
| refit        | stateless                                                     |
| dependencies | numpy, scipy.optimize                                         |

**What it does.** Equal Risk Contribution: solve for weights such
that each instrument contributes equally to total portfolio
variance. Objective: minimise `sum_i (w_i × (Sigma w)_i - 1/n)^2`
via SLSQP with linear inequality constraints (per-block,
per-position).

**Parameters** (config `portfolio.construction.erc`):

- `max_iterations: 1000`
- `convergence_tol: 1e-6`

Plus the shared `ConstraintConfig`:
`target_portfolio_vol = 0.12`, `max_block_weight = 0.40`,
`max_position_weight = 0.15`, `min_positions = 3`,
`min_composite_score_threshold = 0.10`.

**Output metadata.** `n_iterations`, `converged` (bool from SLSQP
result), `objective_final`.

**Strengths.** Risk-balanced by construction; ignores expected
return estimation error (robust); the institutional standard for
diversified portfolios.

**Weaknesses.** Doesn't use composite *magnitude* — only sign.
Two equally-confident positions get the same risk share even if
one has stronger evidence.

### `portfolio.hrp.v1` — SHADOW

| Field        | Value                                                         |
| ------------ | ------------------------------------------------------------- |
| method_id    | `portfolio.hrp.v1`                                            |
| component    | `portfolio_construction`                                      |
| version      | `1.0.0`                                                       |
| status       | `SHADOW`                                                      |
| refit        | stateless                                                     |
| dependencies | numpy, scipy.cluster, scipy.spatial                           |

**What it does.** Hierarchical Risk Parity per Lopez de Prado
2016:

1. **Tree clustering**: single-linkage clustering on the
   correlation-distance matrix (`sqrt(0.5 * (1 - corr))`).
2. **Quasi-diagonalisation**: in-order traversal of the linkage
   tree yields an order that puts similar instruments adjacent.
3. **Recursive bisection**: starting from the quasi-diag order,
   split into halves; allocate inverse-variance-weighted across
   the two clusters. Recurse.

**Parameters** (config `portfolio.construction.hrp`):

- `linkage_method: "single"`

**Output metadata.** `linkage_method`.

**Strengths.** Avoids matrix inversion (more robust than ERC under
ill-conditioned covariance). Respects implicit cluster structure
naturally — e.g. groups all energy together even when not
explicitly told about asset class blocks.

**Weaknesses.** No closed-form objective; sensitivity to the
linkage choice. Single-linkage is the standard but Ward / average
linkage produce subtly different trees on commodity data.

### `portfolio.black_litterman.v1` — SHADOW

| Field        | Value                                                         |
| ------------ | ------------------------------------------------------------- |
| method_id    | `portfolio.black_litterman.v1`                                |
| component    | `portfolio_construction`                                      |
| version      | `1.0.0`                                                       |
| status       | `SHADOW`                                                      |
| refit        | stateless                                                     |
| dependencies | numpy, scipy.linalg                                           |

**What it does.** Bayesian combination of an equal-weight
equilibrium prior with composite-score-derived views:

1. **Prior**: `pi = lambda × Sigma × w_eq` (implied equilibrium
   returns from equal-weight market).
2. **Views**: per-instrument view returns
   `q_i = composite_score_i × annual_vol_i`. View matrix P is
   identity.
3. **View uncertainty**: `Omega_diag_i = tau × (P × Sigma × P^T)_ii /
   confidence_i`. Low composite confidence → high omega → prior
   dominates.
4. **Posterior**: He-Litterman closed form.
5. **Mean-variance**: weights `= (1 / lambda) × Sigma^{-1} ×
   posterior_mean`. Composite-sign overlay applied post-solve.

**Parameters** (config `portfolio.construction.black_litterman`):

- `tau: 0.05` (prior confidence; smaller → stronger views)
- `risk_aversion: 2.5` (typical institutional value)
- `view_confidence_floor: 0.1` (don't let zero-confidence views
  dominate via division-by-zero)

**Output metadata.** `tau`, `risk_aversion`, `view_confidence_floor`.

**Strengths.** Bayesian framework directly quantifies how much to
trust each composite signal. Tunable prior-vs-view balance via
tau.

**Weaknesses.** Most parameters to tune of any method. Posterior
mean only (no full posterior averaging — see `tradeoffs.md` §8).

### `portfolio.cvar.v1` — SHADOW

| Field        | Value                                                         |
| ------------ | ------------------------------------------------------------- |
| method_id    | `portfolio.cvar.v1`                                           |
| component    | `portfolio_construction`                                      |
| version      | `1.0.0`                                                       |
| status       | `SHADOW`                                                      |
| refit        | stateless                                                     |
| dependencies | numpy, scipy.optimize, scipy.linalg                           |

**What it does.** Mean-CVaR optimisation via the Rockafellar-
Uryasev LP formulation:

- 5000 Monte Carlo loss scenarios generated from the covariance
  Cholesky (daily-scaled).
- LP variables: `[w (n), VaR (1), u (S)]` where `u_i = max(0,
  scenario_i_loss - VaR)`.
- Minimise `VaR + (1/(alpha × S)) × sum(u_i)` subject to
  `u_i + scenario_i × w + VaR >= 0`, `sum(w) = 1`, per-block /
  per-position caps.
- Solved via scipy.optimize.linprog (highs method).

**Parameters** (config `portfolio.construction.cvar`):

- `alpha: 0.05` (5% tail; CVaR is the expected loss in the
  worst 5% of cases)
- `lookback_days: 504`

**Output metadata.** `alpha`, `n_scenarios` (5000), `lp_status`
(scipy's solver message).

**Strengths.** Directly minimises tail risk rather than variance.
Matters more for drawdown-averse strategies; variance treats up
+ down moves symmetrically.

**Weaknesses.** LP can be infeasible at small universes (see
`tradeoffs.md` §6). Monte Carlo scenarios are sampled from the
covariance (Gaussian); real return distributions have heavier
tails. Production should ideally use historical bootstrap, which
requires the Stage 9 backtester to verify return distribution
matches.

## Registration

`src/macro_trader/portfolio/covariance/register.py:register()`:

```python
register_method(LedoitWolfCovariance(), MethodStatus.BASELINE, ...)
if _arch_available():
    register_method(DCCGARCHCovariance(), MethodStatus.SHADOW, ...)
```

`src/macro_trader/portfolio/construction/register.py:register()`:

```python
register_method(EqualRiskContributionPortfolio(), MethodStatus.BASELINE, ...)
register_method(HierarchicalRiskParityPortfolio(), MethodStatus.SHADOW, ...)
register_method(BlackLittermanPortfolio(), MethodStatus.SHADOW, ...)
register_method(CVaRPortfolio(), MethodStatus.SHADOW, ...)
```

Both wired into `methods/setup.py:register_all_methods()` under
their respective component names. Idempotent.

## Designation

`config/base.yaml`:

```yaml
signals:
  designated_per_component:
    covariance_estimate: covariance.ledoit_wolf.v1
    portfolio_construction: portfolio.erc.v1
```

`signals.designated.resolve_id("covariance_estimate")` returns
`covariance.ledoit_wolf.v1`; resolve_id for `portfolio_construction`
returns `portfolio.erc.v1`. The portfolio runner uses these to
pick which method's covariance feeds the optimisation.

## State persistence

- `covariance.dcc_garch.v1`: state (per-instrument GARCH params,
  Q_bar, Q_last, alpha, beta) pickled to
  `system.methods_registry.serialized_blob`. ~50 KB; zlib-compressed
  when > 32 KB.
- All 4 portfolio methods: stateless (no fitted state). The
  "training" is the daily covariance + composite snapshot.

## Compute cost estimates

| method                          | daily cost (13 instruments) |
| ------------------------------- | ---------------------------- |
| `covariance.ledoit_wolf.v1`     | <100 ms                      |
| `covariance.dcc_garch.v1`       | ~5 s (weekly fit ~30 s)      |
| `portfolio.erc.v1`              | ~200 ms (SLSQP solve)        |
| `portfolio.hrp.v1`              | ~50 ms (tree + bisection)    |
| `portfolio.black_litterman.v1`  | ~50 ms (closed-form)         |
| `portfolio.cvar.v1`             | ~500 ms (LP with 5000 rows)  |

Daily run total: ~6 s. Weekly DCC refit: ~30 s. Negligible
operationally.
