# Build plan — 13 stages

This document is the **single source of truth** for the build plan. Each stage
gets its own notes folder (`notes/stage_N/`) with `decisions.md`,
`architecture.md`, `tradeoffs.md`, `usage.md`, `methods_registry.md`,
`comparison_results.md`, and `next.md`.

| Stage | Name                                | Focus                                                                                   |
| ----- | ----------------------------------- | --------------------------------------------------------------------------------------- |
| 1     | Foundation                          | Project skeleton, infra, **methods comparison framework**, no business logic            |
| 2     | Data Layer                          | Free-source ingestion, point-in-time, calendar events, data quality (z-score vs IsoForest) |
| 3     | Signal Library Part 1               | Trend, carry, value                                                                     |
| 4     | Signal Library Part 2               | COT positioning; macro factor exposure (OLS vs RF/Causal Forest); dislocation (PCA vs DFM); catalyst sensitivity |
| 5     | Signal Library Part 3               | Vol surface (raw vs SVI); nowcasting (simple vs BVAR); alt-data signals                 |
| 6     | Regime Classifier                   | Rules baseline; HMM, GMM, Markov-Switching, BOCPD enhancements                          |
| 7     | Signal Combination                  | Linear baseline; Bayesian hierarchical / GBM enhancements                               |
| 8     | Portfolio Construction & Risk       | Sample cov vs Ledoit-Wolf / DCC-GARCH; ERC vs HRP / CVaR / Black-Litterman              |
| 9     | Industrial Backtester               | Walk-forward vs CPCV; deflated Sharpe; Monte Carlo; block bootstrap; stress scenarios   |
| 10    | Claude Analysis Layer               | Daily brief, weekly review, per-position deep-dive, per-asset-class brief, monthly outlook; CB tone scoring; news entity extraction; event triggers |
| 11    | Dashboard                           | React + FastAPI, 7 views + Methods page (cross-cutting)                                 |
| 12    | Execution Skeleton & Paper Trading  | Broker interface, order management, paper trading, reconciliation                       |
| 13    | Integration, Monitoring, Polish     | Concept drift, performance attribution clustering, signal decay alerts, runbook        |

## Stage gate

Each stage is "done" when:
- All tests pass; lint clean.
- Notes folder is complete and substantive.
- Any new method is registered through the methods framework with explicit
  status; any enhancement registered as `shadow`.
- A logical-checkpoint git tag is created (`stage-N-<slug>`).

## Universe

Energy: WTI (CL), Brent (BZ), nat gas (NG), heating oil (HO), gasoline (RB).
Base metals: copper (HG), aluminum (LME proxy).
Precious: gold (GC), silver (SI), platinum (PL).
Agriculture: corn (ZC), soybeans (ZS), wheat (ZW).

Free ETF proxies for Stage 2: USO, BNO, UNG, UHN, UGA, CPER, JJU, GLD, SLV, PPLT, CORN, SOYB, WEAT.
