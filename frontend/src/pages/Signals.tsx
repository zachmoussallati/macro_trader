import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  apiMethods,
  type DecayPoint,
  type HeatmapCell,
  type SignalMeta,
  type SignalValueRow,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type Tab = "heatmap" | "detail" | "decay";

const TABS: { key: Tab; label: string }[] = [
  { key: "heatmap", label: "Heatmap" },
  { key: "detail", label: "Detail" },
  { key: "decay", label: "Decay" },
];

const COMPONENT_ORDER: Array<{ key: string; label: string }> = [
  { key: "trend_signal", label: "Trend" },
  { key: "carry_signal", label: "Carry" },
  { key: "value_signal", label: "Value" },
  { key: "positioning_signal", label: "Positioning" },
  { key: "dislocation_signal", label: "Dislocation" },
];

export default function Signals() {
  const [tab, setTab] = useState<Tab>("heatmap");
  return (
    <div className="mx-auto max-w-6xl p-8 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Signals</h1>
          <p className="text-muted-foreground text-sm">
            Cross-component signal values + comparator agreement + rolling Sharpe decay.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/">Home</Link>
        </Button>
      </header>

      <div className="flex gap-2 border-b">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t.key
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "heatmap" && <HeatmapTab />}
      {tab === "detail" && <DetailTab />}
      {tab === "decay" && <DecayTab />}
    </div>
  );
}

// =====================================================================
// Heatmap tab
// =====================================================================
function cellColor(zscore: number | null, confidence: number | null): string {
  if (zscore === null || confidence === null || confidence === 0) return "bg-muted";
  const intensity = Math.min(Math.abs(zscore), 2) / 2;
  const opacity = Math.max(0.15, Math.min(1, confidence));
  if (zscore > 0) {
    return `bg-emerald-500/[${(intensity * opacity).toFixed(2)}]`;
  }
  return `bg-rose-500/[${(intensity * opacity).toFixed(2)}]`;
}

function HeatmapTab() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["signals.heatmap"],
    queryFn: apiMethods.signalHeatmap,
  });
  const instruments = useQuery({
    queryKey: ["data.instruments"],
    queryFn: apiMethods.listInstruments,
  });

  const matrix = useMemo(() => {
    const byKey: Record<string, HeatmapCell> = {};
    for (const c of data ?? []) {
      byKey[`${c.instrument_id}|${c.component}`] = c;
    }
    return byKey;
  }, [data]);

  if (isLoading || instruments.isLoading) return <p className="text-sm">Loading…</p>;
  if (isError || instruments.isError)
    return <p className="text-sm text-destructive">Could not load heatmap.</p>;

  const rows = (instruments.data ?? []).map((i) => i.instrument_id);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Signal heatmap</CardTitle>
        <CardDescription>
          Latest cell per (component, instrument). Green = positive (long bias), red = negative.
          Cell intensity = |z-score|; opacity scales with confidence.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>instrument</TableHead>
              {COMPONENT_ORDER.map((c) => (
                <TableHead key={c.key}>{c.label}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((instrument_id) => (
              <TableRow key={instrument_id}>
                <TableCell className="font-mono text-xs">{instrument_id}</TableCell>
                {COMPONENT_ORDER.map((c) => {
                  const cell = matrix[`${instrument_id}|${c.key}`];
                  if (!cell)
                    return (
                      <TableCell key={c.key} className="text-xs text-muted-foreground">
                        —
                      </TableCell>
                    );
                  const z = cell.zscore ?? 0;
                  return (
                    <TableCell key={c.key}>
                      <div
                        className={`rounded px-2 py-1 text-xs ${cellColor(cell.zscore, cell.confidence)}`}
                        title={`z=${z.toFixed(2)} · conf=${cell.confidence?.toFixed(2) ?? "—"}`}
                      >
                        {cell.raw_value !== null ? cell.raw_value.toFixed(3) : "—"}
                        <span className="ml-1 text-muted-foreground">
                          (z {z >= 0 ? "+" : ""}
                          {z.toFixed(1)})
                        </span>
                      </div>
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// =====================================================================
// Detail tab
// =====================================================================
function DetailTab() {
  const instruments = useQuery({
    queryKey: ["data.instruments"],
    queryFn: apiMethods.listInstruments,
  });
  const [instrument, setInstrument] = useState<string>("");

  const values = useQuery({
    queryKey: ["signals.values", instrument],
    queryFn: () => apiMethods.latestSignalValues(instrument || undefined),
    enabled: instrument !== "",
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Per-instrument detail</CardTitle>
          <CardDescription>Latest signal values across components.</CardDescription>
        </CardHeader>
        <CardContent>
          <select
            value={instrument}
            onChange={(e) => setInstrument(e.target.value)}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="">Choose instrument…</option>
            {(instruments.data ?? []).map((i) => (
              <option key={i.instrument_id} value={i.instrument_id}>
                {i.instrument_id} — {i.name}
              </option>
            ))}
          </select>
        </CardContent>
      </Card>

      {instrument && values.isLoading && <p className="text-sm">Loading…</p>}
      {instrument && values.isError && (
        <p className="text-sm text-destructive">Could not load values.</p>
      )}

      {instrument && values.data && values.data.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No signal values yet</CardTitle>
            <CardDescription>
              Run the <code>compute_all_signals_job</code> in Dagster to populate.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {instrument && values.data && values.data.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{instrument} — latest signal values</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>signal_id</TableHead>
                  <TableHead>raw</TableHead>
                  <TableHead>z-score</TableHead>
                  <TableHead>rank</TableHead>
                  <TableHead>confidence</TableHead>
                  <TableHead>rolling Sharpe</TableHead>
                  <TableHead>value_ts</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {values.data.map((v: SignalValueRow) => (
                  <TableRow key={v.signal_id}>
                    <TableCell className="font-mono text-xs">{v.signal_id}</TableCell>
                    <TableCell className="text-xs">
                      {v.raw_value !== null ? v.raw_value.toFixed(4) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {v.zscore !== null ? v.zscore.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {v.rank !== null ? v.rank.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {v.confidence !== null ? v.confidence.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {v.rolling_sharpe_252 !== null
                        ? v.rolling_sharpe_252.toFixed(2)
                        : "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(v.value_ts).toLocaleDateString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// =====================================================================
// Decay tab
// =====================================================================
function DecayTab() {
  const signals = useQuery({
    queryKey: ["signals.list"],
    queryFn: apiMethods.listSignals,
  });
  const [signalId, setSignalId] = useState<string>("");

  const decay = useQuery({
    queryKey: ["signals.decay", signalId],
    queryFn: () => apiMethods.signalDecay(signalId, 365),
    enabled: signalId !== "",
  });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Signal decay</CardTitle>
          <CardDescription>
            In-sample rolling 252-day Sharpe of the implied position series. Stage 9's backtester
            replaces this with proper walk-forward Sharpe.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <select
            value={signalId}
            onChange={(e) => setSignalId(e.target.value)}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
          >
            <option value="">Choose signal…</option>
            {(signals.data ?? []).map((s: SignalMeta) => (
              <option key={s.signal_id} value={s.signal_id}>
                {s.signal_id} — {s.status}
              </option>
            ))}
          </select>
        </CardContent>
      </Card>

      {signalId && decay.isLoading && <p className="text-sm">Loading…</p>}
      {signalId && decay.data && decay.data.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No decay points yet — run the daily signal job to populate.
        </p>
      )}
      {signalId && decay.data && decay.data.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{signalId}</CardTitle>
            <CardDescription>{decay.data.length} points</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>value_ts</TableHead>
                  <TableHead>rolling Sharpe (252d)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {decay.data.slice(-50).map((p: DecayPoint, i: number) => (
                  <TableRow key={`${p.value_ts}-${i}`}>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(p.value_ts).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-xs">
                      {p.rolling_sharpe_252 !== null
                        ? p.rolling_sharpe_252.toFixed(3)
                        : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
