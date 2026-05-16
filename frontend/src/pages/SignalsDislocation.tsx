/**
 * /signals/dislocation — PCA vs DFM factor model inspector (Stage 4C).
 *
 * Layout:
 * - Header: method toggle (pca / dfm / both).
 * - Top: per-instrument residual table (raw_value, rank, confidence).
 * - Middle: explained-variance time series for the chosen method(s).
 *           PCA steps weekly; DFM evolves smoothly.
 * - Bottom: bar chart of |raw_value| per instrument, sorted descending.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiMethods } from "@/api/client";
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

const METHODS = [
  { id: "dislocation.pca.v1", label: "PCA (baseline)" },
  { id: "dislocation.dfm.v1", label: "DFM (shadow)" },
];

export default function SignalsDislocation() {
  const [methodId, setMethodId] = useState<string>(METHODS[0].id);

  const factors = useQuery({
    queryKey: ["signals.dislocation.factors", methodId],
    queryFn: () => apiMethods.dislocationFactors(methodId),
  });
  const explained = useQuery({
    queryKey: ["signals.dislocation.explained_variance", methodId],
    queryFn: () => apiMethods.dislocationExplainedVariance(methodId, {}),
  });

  const sortedByMagnitude = useMemo(() => {
    if (!factors.data) return [];
    return [...factors.data]
      .filter((f) => f.raw_value !== null)
      .sort(
        (a, b) =>
          Math.abs(b.raw_value ?? 0) - Math.abs(a.raw_value ?? 0),
      );
  }, [factors.data]);

  const evChart = useMemo(
    () =>
      (explained.data ?? []).map((p) => ({
        value_ts: new Date(p.value_ts).toLocaleDateString(),
        explained_variance: p.explained_variance,
      })),
    [explained.data],
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Dislocation</h1>
          <p className="text-sm text-muted-foreground">
            Cross-asset residuals against the top-K factor reconstruction.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/signals">Back to signals</Link>
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Method</CardTitle>
          <CardDescription>
            PCA refits weekly with sign alignment; DFM Kalman-filtered.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <select
            value={methodId}
            onChange={(e) => setMethodId(e.target.value)}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
            aria-label="method"
          >
            {METHODS.map((m) => (
              <option key={m.id} value={m.id}>
                {m.label}
              </option>
            ))}
          </select>
        </CardContent>
      </Card>

      {factors.isLoading && <p className="text-sm">Loading dislocation snapshot…</p>}
      {factors.isError && (
        <p className="text-sm text-destructive">Could not load dislocation snapshot.</p>
      )}
      {factors.data && factors.data.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No dislocation data yet</CardTitle>
            <CardDescription>
              The {methodId} method hasn't produced any signal values yet.
              For DFM, convergence on short backfills may have failed —
              check the dislocation_models_refit asset logs.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {factors.data && factors.data.length > 0 && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Current dislocation by instrument</CardTitle>
              <CardDescription>
                |raw_value| sorted descending. Bars colored by sign.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div style={{ width: "100%", height: 280 }}>
                <ResponsiveContainer>
                  <BarChart
                    data={sortedByMagnitude.map((f) => ({
                      instrument_id: f.instrument_id,
                      raw_value: f.raw_value,
                    }))}
                    margin={{ top: 10, right: 20, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="instrument_id" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 10 }} domain={[-1, 1]} />
                    <Tooltip />
                    <Bar dataKey="raw_value" fill="#3b82f6" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Explained variance over time</CardTitle>
              <CardDescription>
                PCA: step changes weekly (after the refit asset runs). DFM:
                smooth evolution.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div style={{ width: "100%", height: 220 }}>
                <ResponsiveContainer>
                  <LineChart
                    data={evChart}
                    margin={{ top: 10, right: 20, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="value_ts" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} domain={[0, 1]} />
                    <Tooltip />
                    <Line
                      type="stepAfter"
                      dataKey="explained_variance"
                      stroke="#3b82f6"
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Per-instrument residuals</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>instrument</TableHead>
                    <TableHead>raw_value</TableHead>
                    <TableHead>z-score</TableHead>
                    <TableHead>rank</TableHead>
                    <TableHead>confidence</TableHead>
                    <TableHead>explained var</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedByMagnitude.map((f) => (
                    <TableRow key={f.instrument_id}>
                      <TableCell className="font-mono text-xs">
                        {f.instrument_id}
                      </TableCell>
                      <TableCell className="text-xs">
                        {f.raw_value !== null ? f.raw_value.toFixed(3) : "—"}
                      </TableCell>
                      <TableCell className="text-xs">
                        {f.zscore !== null ? f.zscore.toFixed(2) : "—"}
                      </TableCell>
                      <TableCell className="text-xs">
                        {f.rank !== null ? f.rank.toFixed(2) : "—"}
                      </TableCell>
                      <TableCell className="text-xs">
                        {f.confidence !== null ? f.confidence.toFixed(2) : "—"}
                      </TableCell>
                      <TableCell className="text-xs">
                        {f.explained_variance !== null
                          ? f.explained_variance.toFixed(2)
                          : "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
