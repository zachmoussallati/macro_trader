/**
 * /portfolio — Stage 8 portfolio sizing home.
 *
 * Layout:
 * - Header: method selector + drawdown gate banner.
 * - Top panel: positions table — instrument, weight, pre-gate weight,
 *   composite score, block, vol contribution.
 * - Middle panel: equity curve sparkline + cumulative return + peak +
 *   drawdown_from_peak.
 * - Right panel: block exposure breakdown.
 * - Bottom panel: drawdown gate state with manual-release button for
 *   level_3.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiMethods,
  type DrawdownState,
  type PortfolioMethodRow,
  type PortfolioPositionRow,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function gateBadgeVariant(level: string): "default" | "secondary" | "destructive" {
  if (level === "level_3") return "destructive";
  if (level === "level_2" || level === "level_1") return "secondary";
  return "default";
}

function weightColor(w: number): string {
  if (w > 0.10) return "bg-emerald-500/40";
  if (w > 0.02) return "bg-emerald-500/20";
  if (w > -0.02) return "bg-muted/30";
  if (w > -0.10) return "bg-rose-500/20";
  return "bg-rose-500/40";
}

export default function Portfolio() {
  const queryClient = useQueryClient();
  const methodsQ = useQuery({
    queryKey: ["portfolio.methods"],
    queryFn: apiMethods.portfolioMethods,
  });
  const [methodId, setMethodId] = useState<string>("portfolio.erc.v1");

  const positionsQ = useQuery({
    queryKey: ["portfolio.positions", methodId],
    queryFn: () => apiMethods.portfolioPositions({ method_id: methodId }),
  });
  const riskQ = useQuery({
    queryKey: ["portfolio.risk", methodId],
    queryFn: () => apiMethods.portfolioRisk({ method_id: methodId }),
  });
  const drawdownQ = useQuery({
    queryKey: ["portfolio.drawdown", methodId],
    queryFn: () => apiMethods.portfolioDrawdown(methodId),
    refetchInterval: 30_000,
  });
  const equityQ = useQuery({
    queryKey: ["portfolio.equity", methodId],
    queryFn: () => apiMethods.portfolioEquity({ method_id: methodId }),
  });

  const releaseMutation = useMutation({
    mutationFn: () => apiMethods.portfolioDrawdownRelease(methodId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["portfolio.drawdown", methodId] });
      queryClient.invalidateQueries({ queryKey: ["portfolio.positions", methodId] });
    },
  });

  const positions = positionsQ.data ?? [];
  const sortedPositions = useMemo(
    () =>
      [...positions].sort(
        (a: PortfolioPositionRow, b: PortfolioPositionRow) =>
          Math.abs(b.target_weight) - Math.abs(a.target_weight),
      ),
    [positions],
  );

  const drawdownMethods = (methodsQ.data ?? []).filter(
    (m: PortfolioMethodRow) => m.component === "portfolio_construction",
  );

  const drawdown: DrawdownState | null = drawdownQ.data ?? null;
  const isLevel3 = drawdown?.current_gate_level === "level_3";
  const equity = equityQ.data ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Portfolio</h1>
          <p className="text-sm text-muted-foreground">
            Stage 8 sized positions from composite scores * portfolio
            method * drawdown gate.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/">Home</Link>
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Method + gate</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          {methodsQ.isLoading && <p className="text-sm">Loading…</p>}
          {drawdownMethods.length > 0 ? (
            <select
              value={methodId}
              onChange={(e) => setMethodId(e.target.value)}
              className="h-10 rounded-md border border-input bg-background px-3 text-sm"
              aria-label="method"
            >
              {drawdownMethods.map((m) => (
                <option key={m.method_id} value={m.method_id}>
                  {m.name} ({m.status})
                </option>
              ))}
            </select>
          ) : (
            <p className="text-sm text-muted-foreground">
              No portfolio methods registered yet. Run methods/setup.py
              or start Dagster.
            </p>
          )}
          {drawdown && (
            <span className="text-xs text-muted-foreground">
              gate{" "}
              <Badge variant={gateBadgeVariant(drawdown.current_gate_level)}>
                {drawdown.current_gate_level}
              </Badge>{" "}
              scaling {drawdown.effective_scaling_factor.toFixed(2)}
            </span>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Positions</CardTitle>
          <CardDescription>
            Ranked by absolute weight; sign comes from composite, magnitude
            from the portfolio method.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {positionsQ.isLoading && <p className="text-sm">Loading…</p>}
          {positions.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No positions yet. Run portfolio_positions_job (next-day
              00:05 UTC).
            </p>
          )}
          {positions.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>instrument</TableHead>
                  <TableHead>target</TableHead>
                  <TableHead>pre-gate</TableHead>
                  <TableHead>vol contrib</TableHead>
                  <TableHead>score</TableHead>
                  <TableHead>block</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedPositions.map((p) => (
                  <TableRow key={p.instrument_id}>
                    <TableCell className="font-mono text-xs">
                      {p.instrument_id}
                    </TableCell>
                    <TableCell>
                      <div
                        className={`inline-block rounded px-2 py-1 text-xs ${weightColor(p.target_weight)}`}
                      >
                        {p.target_weight.toFixed(3)}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">
                      {p.pre_gate_weight !== null
                        ? p.pre_gate_weight.toFixed(3)
                        : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {p.expected_vol_contribution !== null
                        ? p.expected_vol_contribution.toFixed(3)
                        : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {p.composite_score !== null
                        ? p.composite_score.toFixed(2)
                        : "—"}
                    </TableCell>
                    <TableCell className="text-xs">{p.block ?? "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>Equity curve</CardTitle>
            <CardDescription>
              {equity.length === 0
                ? "No equity history yet — runs build up after the daily portfolio job."
                : `${equity.length} days; latest NAV ${equity[equity.length - 1].nav.toFixed(4)}`}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {equity.length > 0 && (
              <>
                <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                  <span>
                    cumulative{" "}
                    <code>
                      {((equity[equity.length - 1].cumulative_return ?? 0) * 100).toFixed(
                        2,
                      )}
                      %
                    </code>
                  </span>
                  <span>
                    peak{" "}
                    <code>
                      {equity[equity.length - 1].peak_nav?.toFixed(4) ?? "—"}
                    </code>
                  </span>
                  <span>
                    drawdown{" "}
                    <code>
                      {(
                        (equity[equity.length - 1].drawdown_from_peak ?? 0) * 100
                      ).toFixed(2)}
                      %
                    </code>
                  </span>
                </div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>date</TableHead>
                      <TableHead>NAV</TableHead>
                      <TableHead>daily</TableHead>
                      <TableHead>cum.</TableHead>
                      <TableHead>peak</TableHead>
                      <TableHead>dd</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {equity
                      .slice(-30)
                      .reverse()
                      .map((p) => (
                        <TableRow key={p.as_of}>
                          <TableCell className="text-xs">
                            {new Date(p.as_of).toLocaleDateString()}
                          </TableCell>
                          <TableCell className="text-xs">
                            {p.nav.toFixed(4)}
                          </TableCell>
                          <TableCell className="text-xs">
                            {p.daily_return !== null
                              ? `${(p.daily_return * 100).toFixed(2)}%`
                              : "—"}
                          </TableCell>
                          <TableCell className="text-xs">
                            {p.cumulative_return !== null
                              ? `${(p.cumulative_return * 100).toFixed(2)}%`
                              : "—"}
                          </TableCell>
                          <TableCell className="text-xs">
                            {p.peak_nav?.toFixed(4) ?? "—"}
                          </TableCell>
                          <TableCell className="text-xs">
                            {p.drawdown_from_peak !== null
                              ? `${(p.drawdown_from_peak * 100).toFixed(2)}%`
                              : "—"}
                          </TableCell>
                        </TableRow>
                      ))}
                  </TableBody>
                </Table>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Block exposure</CardTitle>
            <CardDescription>
              Fraction of gross exposure per asset class. Cap 40%.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {riskQ.data && Object.keys(riskQ.data.block_exposure).length > 0 ? (
              <ul className="space-y-1 text-sm">
                {Object.entries(riskQ.data.block_exposure)
                  .sort((a, b) => b[1] - a[1])
                  .map(([block, share]) => (
                    <li key={block} className="flex justify-between">
                      <span>{block}</span>
                      <span className="font-mono text-xs">
                        {(share * 100).toFixed(1)}%
                      </span>
                    </li>
                  ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">
                No block exposure data yet.
              </p>
            )}
            {riskQ.data?.portfolio_vol !== undefined && riskQ.data.portfolio_vol !== null && (
              <p className="mt-3 text-xs text-muted-foreground">
                Expected portfolio vol{" "}
                <code>{(riskQ.data.portfolio_vol * 100).toFixed(2)}%</code>
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Drawdown gate</CardTitle>
          <CardDescription>
            Staged risk discipline. Level 1 (-5% daily) → 0.5x for 3 days.
            Level 2 (-8% over 5 days) → 0.3x for 10 days. Level 3 (-10%
            peak) → 0x manual release only.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {drawdown && (
            <div className="space-y-1 text-sm">
              <p>
                Level{" "}
                <Badge variant={gateBadgeVariant(drawdown.current_gate_level)}>
                  {drawdown.current_gate_level}
                </Badge>
                {" · "}
                effective scaling{" "}
                <code>{drawdown.effective_scaling_factor.toFixed(2)}</code>
              </p>
              {drawdown.level_1_triggered_at && (
                <p className="text-xs text-muted-foreground">
                  L1 triggered {new Date(drawdown.level_1_triggered_at).toLocaleString()};
                  release{" "}
                  {drawdown.level_1_release_at
                    ? new Date(drawdown.level_1_release_at).toLocaleString()
                    : "—"}
                </p>
              )}
              {drawdown.level_2_triggered_at && (
                <p className="text-xs text-muted-foreground">
                  L2 triggered {new Date(drawdown.level_2_triggered_at).toLocaleString()};
                  release{" "}
                  {drawdown.level_2_release_at
                    ? new Date(drawdown.level_2_release_at).toLocaleString()
                    : "—"}
                </p>
              )}
              {drawdown.level_3_triggered_at && (
                <p className="text-xs text-rose-600">
                  L3 triggered {new Date(drawdown.level_3_triggered_at).toLocaleString()} —
                  manual release required
                </p>
              )}
            </div>
          )}
          {isLevel3 && (
            <Button
              variant="destructive"
              size="sm"
              disabled={releaseMutation.isPending}
              onClick={() => releaseMutation.mutate()}
            >
              {releaseMutation.isPending ? "Releasing…" : "Manually release L3 gate"}
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
