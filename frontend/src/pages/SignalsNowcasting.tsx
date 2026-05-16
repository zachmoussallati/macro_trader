/**
 * /signals/nowcasting — Stage 5 follow-up.
 *
 * Layout:
 * - Header: method toggle (OLS-AR / BVAR).
 * - Top: projections table (one row per release).
 * - Bottom: per-release history chart (when a release is selected).
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
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

const METHODS = [
  { id: "nowcasting.ols_ar.v1", label: "OLS-AR (baseline)" },
  { id: "nowcasting.bvar.v1", label: "BVAR (shadow)" },
];

export default function SignalsNowcasting() {
  const [methodId, setMethodId] = useState<string>(METHODS[0].id);
  const [selectedRelease, setSelectedRelease] = useState<string | null>(null);

  const projections = useQuery({
    queryKey: ["signals.nowcasting.projections", methodId],
    queryFn: () => apiMethods.nowcastingProjections(methodId),
  });
  const history = useQuery({
    queryKey: ["signals.nowcasting.history", selectedRelease],
    queryFn: () => apiMethods.nowcastingHistory(selectedRelease as string),
    enabled: selectedRelease !== null,
  });

  const historyChart = useMemo(
    () =>
      (history.data ?? []).map((p) => ({
        value_ts: new Date(p.value_ts).toLocaleDateString(),
        actual: p.actual,
      })),
    [history.data],
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Nowcasting</h1>
          <p className="text-sm text-muted-foreground">
            Per-release projections vs last actual; surprise z-scored and
            mapped onto affected instruments.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/signals">Back to signals</Link>
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Method</CardTitle>
        </CardHeader>
        <CardContent>
          <select
            value={methodId}
            onChange={(e) => {
              setMethodId(e.target.value);
              setSelectedRelease(null);
            }}
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

      {projections.isLoading && <p className="text-sm">Loading projections…</p>}
      {projections.isError && (
        <p className="text-sm text-destructive">Could not load projections.</p>
      )}
      {projections.data && projections.data.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No projections yet</CardTitle>
            <CardDescription>
              Run <code>nowcasting_refit_job</code> in Dagster to populate
              fitted state for each release.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {projections.data && projections.data.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Per-release projections</CardTitle>
            <CardDescription>
              Click a row to load that release's history below.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>release</TableHead>
                  <TableHead>name</TableHead>
                  <TableHead>last actual</TableHead>
                  <TableHead>nowcast</TableHead>
                  <TableHead>surprise z</TableHead>
                  <TableHead>affects</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {projections.data.map((p) => (
                  <TableRow
                    key={p.release_id}
                    onClick={() => setSelectedRelease(p.release_id)}
                    className={
                      "cursor-pointer hover:bg-muted/50 " +
                      (selectedRelease === p.release_id ? "bg-muted" : "")
                    }
                  >
                    <TableCell className="font-mono text-xs">
                      {p.release_id}
                    </TableCell>
                    <TableCell className="text-xs">{p.name}</TableCell>
                    <TableCell className="text-xs">
                      {p.last_actual !== null ? p.last_actual.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {p.pred_mean !== null ? p.pred_mean.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {p.surprise_z !== null ? (
                        <Badge
                          variant={
                            Math.abs(p.surprise_z) > 1 ? "destructive" : "outline"
                          }
                        >
                          {p.surprise_z.toFixed(2)}
                        </Badge>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {p.affected_instruments.join(", ") || "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {selectedRelease && (
        <Card>
          <CardHeader>
            <CardTitle>{selectedRelease} — historical releases</CardTitle>
            <CardDescription>
              5-year actual-release series. Stage 5 ships actuals only;
              historical nowcasts are persisted starting Stage 6.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {history.isLoading && <p className="text-sm">Loading…</p>}
            {history.data && history.data.length > 0 && (
              <div style={{ width: "100%", height: 220 }}>
                <ResponsiveContainer>
                  <LineChart
                    data={historyChart}
                    margin={{ top: 10, right: 20, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="value_ts" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip />
                    <Line
                      type="monotone"
                      dataKey="actual"
                      stroke="#3b82f6"
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
            {history.data && history.data.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No history for this release in the lookback.
              </p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
