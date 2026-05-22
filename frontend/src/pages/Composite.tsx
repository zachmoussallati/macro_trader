/**
 * /composite — Stage 7 composite-scoring home.
 *
 * Layout:
 * - Header: method selector (linear / bayesian_hier / gbm).
 * - Top: ranked instrument table — score descending. Click a row to
 *        load its breakdown into the middle panel.
 * - Middle: per-signal contribution decomposition for the selected
 *        instrument. Signed horizontal bars; metadata footer shows
 *        regime label + transition multiplier + cp probability.
 * - Bottom: effective weights table — rows = signal methods, columns =
 *        regime probability vector. Shows where today's conviction
 *        sources from.
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

function scoreColor(score: number | null): string {
  if (score === null) return "bg-muted/30";
  if (score > 0.5) return "bg-emerald-500/50";
  if (score > 0.1) return "bg-emerald-500/20";
  if (score > -0.1) return "bg-muted/30";
  if (score > -0.5) return "bg-rose-500/20";
  return "bg-rose-500/50";
}

function contributionBar(contribution: number, maxAbs: number): JSX.Element {
  const width = Math.min(100, (Math.abs(contribution) / Math.max(maxAbs, 1e-6)) * 100);
  const colour = contribution >= 0 ? "bg-emerald-500/60" : "bg-rose-500/60";
  const align = contribution >= 0 ? "left-1/2" : "right-1/2";
  return (
    <div className="relative h-3 w-full overflow-hidden rounded bg-muted/30">
      <div
        className={`absolute top-0 ${align} h-3 ${colour}`}
        style={{ width: `${width / 2}%` }}
      />
    </div>
  );
}

export default function Composite() {
  const methodsQ = useQuery({
    queryKey: ["composite.methods"],
    queryFn: apiMethods.compositeMethods,
  });
  const [methodId, setMethodId] = useState<string>("composite.linear.v1");
  const [instrument, setInstrument] = useState<string | null>(null);

  const scoresQ = useQuery({
    queryKey: ["composite.scores", methodId],
    queryFn: () => apiMethods.compositeScores({ method_id: methodId }),
  });
  const weightsQ = useQuery({
    queryKey: ["composite.weights", methodId],
    queryFn: () => apiMethods.compositeWeights({ method_id: methodId }),
  });
  const breakdownQ = useQuery({
    queryKey: ["composite.breakdown", methodId, instrument],
    queryFn: () =>
      instrument
        ? apiMethods.compositeBreakdown(instrument, { method_id: methodId })
        : Promise.resolve(null),
    enabled: !!instrument,
  });
  const transitionQ = useQuery({
    queryKey: ["composite.transition"],
    queryFn: () => apiMethods.compositeTransitionMultiplier({}),
  });

  // Pivot per_regime weights into a signal × regime grid.
  const weightGrid = useMemo(() => {
    if (!weightsQ.data) return [];
    const bySignal: Record<string, Record<string, number>> = {};
    for (const w of weightsQ.data.per_regime) {
      if (!bySignal[w.signal_method_id]) bySignal[w.signal_method_id] = {};
      bySignal[w.signal_method_id][w.regime_label] = w.weight;
    }
    return Object.entries(bySignal).map(([signal, byRegime]) => ({
      signal,
      byRegime,
    }));
  }, [weightsQ.data]);

  const maxContributionAbs = useMemo(() => {
    if (!breakdownQ.data) return 0;
    return Math.max(
      ...breakdownQ.data.contributions.map((c) => Math.abs(c.contribution)),
      1e-6,
    );
  }, [breakdownQ.data]);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Composite</h1>
          <p className="text-sm text-muted-foreground">
            Stage 7 regime-conditional composite scoring. Combines 10 signal
            families with per-(regime, signal) attribution weights and BOCPD
            changepoint-probability dampening.
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
        <CardContent className="flex flex-wrap items-center gap-3">
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
              No composite methods registered yet. Run methods/setup.py or
              start Dagster so register_all_methods runs.
            </p>
          )}
          {transitionQ.data && (
            <span className="text-xs text-muted-foreground">
              conviction multiplier{" "}
              <code>{transitionQ.data.multiplier.toFixed(2)}</code>
              {transitionQ.data.changepoint_probability !== null && (
                <>
                  {" "}
                  · cp{" "}
                  <code>
                    {transitionQ.data.changepoint_probability.toFixed(3)}
                  </code>
                </>
              )}
            </span>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Ranked instruments</CardTitle>
          <CardDescription>
            Click an instrument to see its per-signal decomposition below.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {scoresQ.isLoading && <p className="text-sm">Loading…</p>}
          {scoresQ.data && scoresQ.data.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No composite scores yet. Run composite_score_job (daily 23:45 UTC).
            </p>
          )}
          {scoresQ.data && scoresQ.data.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>instrument</TableHead>
                  <TableHead>score</TableHead>
                  <TableHead>confidence</TableHead>
                  <TableHead>regime</TableHead>
                  <TableHead>n_signals</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {scoresQ.data.map((r) => (
                  <TableRow
                    key={r.instrument_id}
                    onClick={() => setInstrument(r.instrument_id)}
                    className={`cursor-pointer hover:bg-muted/40 ${
                      instrument === r.instrument_id ? "bg-muted/40" : ""
                    }`}
                  >
                    <TableCell className="font-mono text-xs">
                      {r.instrument_id}
                    </TableCell>
                    <TableCell>
                      <div
                        className={`inline-block rounded px-2 py-1 text-xs ${scoreColor(r.score)}`}
                      >
                        {r.score !== null ? r.score.toFixed(2) : "—"}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs">
                      {r.confidence !== null ? r.confidence.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {r.regime_label ?? "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {r.n_signals_used ?? "—"}
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
          <CardTitle>Breakdown</CardTitle>
          <CardDescription>
            {instrument
              ? `Per-signal contribution for ${instrument}.`
              : "Select an instrument above to inspect its decomposition."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!instrument && (
            <p className="text-sm text-muted-foreground">
              Pick a row from the ranked table.
            </p>
          )}
          {instrument && breakdownQ.isLoading && (
            <p className="text-sm">Loading…</p>
          )}
          {instrument && breakdownQ.data && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                <Badge variant="default">{breakdownQ.data.regime_label ?? "—"}</Badge>
                <span>
                  raw_score{" "}
                  <code>
                    {breakdownQ.data.raw_score !== null
                      ? breakdownQ.data.raw_score.toFixed(3)
                      : "—"}
                  </code>
                </span>
                <span>
                  score{" "}
                  <code>
                    {breakdownQ.data.score !== null
                      ? breakdownQ.data.score.toFixed(3)
                      : "—"}
                  </code>
                </span>
                {breakdownQ.data.transition_multiplier !== null && (
                  <span>
                    multiplier{" "}
                    <code>
                      {breakdownQ.data.transition_multiplier.toFixed(2)}
                    </code>
                  </span>
                )}
                {breakdownQ.data.transition_probability !== null && (
                  <span>
                    cp{" "}
                    <code>
                      {breakdownQ.data.transition_probability.toFixed(3)}
                    </code>
                  </span>
                )}
              </div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>signal</TableHead>
                    <TableHead>weight</TableHead>
                    <TableHead>z</TableHead>
                    <TableHead>confidence</TableHead>
                    <TableHead>contribution</TableHead>
                    <TableHead className="w-48">bar</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {breakdownQ.data.contributions.map((c) => (
                    <TableRow key={c.signal_method_id}>
                      <TableCell className="font-mono text-xs">
                        {c.signal_method_id}
                      </TableCell>
                      <TableCell className="text-xs">
                        {c.weight.toFixed(3)}
                      </TableCell>
                      <TableCell className="text-xs">{c.z.toFixed(2)}</TableCell>
                      <TableCell className="text-xs">
                        {c.confidence.toFixed(2)}
                      </TableCell>
                      <TableCell className="text-xs">
                        {c.contribution.toFixed(3)}
                      </TableCell>
                      <TableCell>
                        {contributionBar(c.contribution, maxContributionAbs)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Weights heatmap</CardTitle>
          <CardDescription>
            Per-regime per-signal weights from the latest snapshot. Effective
            weights below collapse this grid against the current regime
            probability vector.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {weightsQ.isLoading && <p className="text-sm">Loading…</p>}
          {weightsQ.data && weightsQ.data.per_regime.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No weight snapshot yet. Run composite_weights_refit_job (weekly
              Sunday 06:00 UTC).
            </p>
          )}
          {weightGrid.length > 0 && (
            <div className="space-y-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>signal</TableHead>
                    {NAMED_REGIMES.map((r) => (
                      <TableHead key={r}>{r}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {weightGrid.map(({ signal, byRegime }) => (
                    <TableRow key={signal}>
                      <TableCell className="font-mono text-xs">{signal}</TableCell>
                      {NAMED_REGIMES.map((r) => {
                        const v = byRegime[r] ?? null;
                        return (
                          <TableCell key={r}>
                            <div
                              className={`rounded px-2 py-1 text-xs ${
                                v === null
                                  ? "bg-muted/30"
                                  : v > 0.3
                                    ? "bg-emerald-500/40"
                                    : v > 0.1
                                      ? "bg-emerald-500/20"
                                      : "bg-muted/30"
                              }`}
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

              {weightsQ.data && weightsQ.data.effective.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
                    Effective weights (probability-weighted blend)
                  </h4>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>signal</TableHead>
                        <TableHead>effective weight</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {weightsQ.data.effective.map((e) => (
                        <TableRow key={e.signal_method_id}>
                          <TableCell className="font-mono text-xs">
                            {e.signal_method_id}
                          </TableCell>
                          <TableCell className="text-xs">
                            {e.effective_weight.toFixed(3)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}

              {weightsQ.data && weightsQ.data.regime_probability_vector && (
                <p className="text-xs text-muted-foreground">
                  Regime probabilities:{" "}
                  {NAMED_REGIMES.map((r) => {
                    const p =
                      weightsQ.data!.regime_probability_vector![r] ?? 0;
                    return (
                      <span key={r} className="mr-2">
                        {r} <code>{p.toFixed(2)}</code>
                      </span>
                    );
                  })}
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
