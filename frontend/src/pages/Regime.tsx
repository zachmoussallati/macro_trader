/**
 * /regime — Stage 6 regime-classifier home.
 *
 * Layout:
 * - Header: method selector across the 5 regime methods.
 * - Top: current regime card — label + probability bar + days
 *        in regime + confidence.
 * - Middle: regime history table — last 60 days of classified
 *        labels.
 * - Bottom: per-regime per-signal attribution heatmap (rows =
 *        signal methods, columns = regimes, cell = Sharpe).
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiMethods } from "@/api/client";
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

const NAMED_REGIMES = [
  "risk_on_growth",
  "risk_off_defensive",
  "stagflation",
  "carry_friendly",
  "vol_spike",
];

function sharpeCellColor(sharpe: number | null): string {
  if (sharpe === null) return "bg-muted/30";
  if (sharpe > 1) return "bg-emerald-500/40";
  if (sharpe > 0.3) return "bg-emerald-500/20";
  if (sharpe > -0.3) return "bg-muted/30";
  if (sharpe > -1) return "bg-rose-500/20";
  return "bg-rose-500/40";
}

export default function Regime() {
  const methodsQ = useQuery({
    queryKey: ["regime.methods"],
    queryFn: apiMethods.regimeMethods,
  });
  const [methodId, setMethodId] = useState<string>("regime.rules.v1");

  const current = useQuery({
    queryKey: ["regime.current", methodId],
    queryFn: () => apiMethods.regimeCurrent(methodId),
  });
  const history = useQuery({
    queryKey: ["regime.history", methodId],
    queryFn: () => apiMethods.regimeHistory(methodId),
  });
  const attribution = useQuery({
    queryKey: ["regime.attribution", methodId],
    queryFn: () => apiMethods.regimeAttribution({ regime_method: methodId }),
  });

  // Group attribution by signal_method_id for the heatmap.
  const attributionMatrix = useMemo(() => {
    if (!attribution.data) return {} as Record<string, Record<string, number | null>>;
    const out: Record<string, Record<string, number | null>> = {};
    for (const r of attribution.data) {
      if (!out[r.signal_method_id]) out[r.signal_method_id] = {};
      out[r.signal_method_id][r.regime_label] = r.sharpe;
    }
    return out;
  }, [attribution.data]);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Regime</h1>
          <p className="text-sm text-muted-foreground">
            Macro regime classification across 5 methods + per-(regime,
            signal) attribution.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/">Home</Link>
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Method</CardTitle>
        </CardHeader>
        <CardContent>
          {methodsQ.isLoading && <p className="text-sm">Loading…</p>}
          {methodsQ.data && methodsQ.data.length > 0 ? (
            <select
              value={methodId}
              onChange={(e) => setMethodId(e.target.value)}
              className="h-10 rounded-md border border-input bg-background px-3 text-sm"
              aria-label="method"
            >
              {methodsQ.data.map((m) => (
                <option key={m.method_id} value={m.method_id}>
                  {m.name} ({m.status})
                </option>
              ))}
            </select>
          ) : (
            <p className="text-sm text-muted-foreground">
              No regime methods registered yet. Run methods/setup.py or
              start Dagster so register_all_methods runs.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Current regime</CardTitle>
          <CardDescription>
            Latest classification from {methodId}.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {current.isLoading && <p className="text-sm">Loading…</p>}
          {current.data === null && (
            <p className="text-sm text-muted-foreground">
              No regime state yet. Run the regime_classification Dagster
              asset to populate regime.regime_states.
            </p>
          )}
          {current.data && (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <Badge variant="default" className="text-base">
                  {current.data.label}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  confidence{" "}
                  {current.data.confidence !== null
                    ? current.data.confidence.toFixed(2)
                    : "—"}
                  {current.data.days_in_regime !== null && (
                    <>
                      {" "}
                      · {current.data.days_in_regime} days in regime
                    </>
                  )}
                  {current.data.transition_prob !== null && (
                    <>
                      {" "}
                      · cp {current.data.transition_prob.toFixed(3)}
                    </>
                  )}
                </span>
              </div>
              <div className="flex h-4 w-full overflow-hidden rounded">
                {NAMED_REGIMES.map((r) => {
                  const p = current.data!.probability_vector[r] ?? 0;
                  return (
                    <div
                      key={r}
                      style={{ width: `${p * 100}%` }}
                      title={`${r}: ${p.toFixed(2)}`}
                      className={
                        r === current.data!.label
                          ? "bg-emerald-500"
                          : "bg-muted-foreground/40"
                      }
                    />
                  );
                })}
              </div>
              <div className="grid grid-cols-5 gap-1 text-[10px] text-muted-foreground">
                {NAMED_REGIMES.map((r) => (
                  <span key={r} className="truncate">
                    {r}
                  </span>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent history</CardTitle>
        </CardHeader>
        <CardContent>
          {history.isLoading && <p className="text-sm">Loading…</p>}
          {history.data && history.data.length === 0 && (
            <p className="text-sm text-muted-foreground">No history yet.</p>
          )}
          {history.data && history.data.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>date</TableHead>
                  <TableHead>label</TableHead>
                  <TableHead>confidence</TableHead>
                  <TableHead>cp_prob</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.data.slice(-60).reverse().map((p) => (
                  <TableRow key={p.value_ts}>
                    <TableCell className="text-xs">
                      {new Date(p.value_ts).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-xs">{p.label}</TableCell>
                    <TableCell className="text-xs">
                      {p.confidence !== null ? p.confidence.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {p.transition_prob !== null
                        ? p.transition_prob.toFixed(3)
                        : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Attribution heatmap</CardTitle>
          <CardDescription>
            Per-regime in-sample Sharpe for each signal method. Stage 7
            composite scoring weights signals by these numbers.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {attribution.isLoading && <p className="text-sm">Loading…</p>}
          {attribution.data && attribution.data.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No attribution rows yet. Run regime_attribution_compute to
              populate regime.regime_attribution.
            </p>
          )}
          {Object.keys(attributionMatrix).length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>signal method</TableHead>
                  {NAMED_REGIMES.map((r) => (
                    <TableHead key={r}>{r}</TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(attributionMatrix).map(([signal, byRegime]) => (
                  <TableRow key={signal}>
                    <TableCell className="font-mono text-xs">{signal}</TableCell>
                    {NAMED_REGIMES.map((r) => {
                      const v = byRegime[r] ?? null;
                      return (
                        <TableCell key={r}>
                          <div
                            className={`rounded px-2 py-1 text-xs ${sharpeCellColor(v)}`}
                            title={v === null ? "no data" : `Sharpe ${v.toFixed(2)}`}
                          >
                            {v !== null ? v.toFixed(2) : "—"}
                          </div>
                        </TableCell>
                      );
                    })}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
