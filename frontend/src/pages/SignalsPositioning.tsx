/**
 * /signals/positioning — per-instrument COT breakdown view (Stage 4C).
 *
 * Layout:
 * - Header: instrument + method selector.
 * - Top: stacked area chart of managed-money long / short / net.
 * - Middle: per-row table with the raw COT breakdown.
 * - Bottom: sparkline of the chosen method's signal values over the
 *           same window.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  apiMethods,
  type PositioningBreakdownPoint,
} from "@/api/client";
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
  { id: "positioning.cot_zscore.v1", label: "Managed money (disagg)" },
  { id: "positioning.cot_commercial.v1", label: "Commercial (legacy)" },
];

export default function SignalsPositioning() {
  const instruments = useQuery({
    queryKey: ["data.instruments"],
    queryFn: apiMethods.listInstruments,
  });
  const [instrument, setInstrument] = useState<string>("");
  const [methodId, setMethodId] = useState<string>(METHODS[0].id);

  const breakdown = useQuery({
    queryKey: ["signals.positioning.breakdown", instrument, methodId],
    queryFn: () =>
      apiMethods.positioningBreakdown(instrument, {
        method_id: methodId,
        lookback_weeks: 156,
      }),
    enabled: instrument !== "",
  });

  const chartData = useMemo(
    () =>
      (breakdown.data ?? []).map((p) => ({
        report_ts: new Date(p.report_ts).toLocaleDateString(),
        long: p.long ?? 0,
        short: -(p.short ?? 0),
        net: p.net ?? 0,
        raw_value: p.raw_value ?? 0,
      })),
    [breakdown.data],
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Positioning</h1>
          <p className="text-sm text-muted-foreground">
            CFTC COT-derived signals: managed-money + commercial extremes.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/signals">Back to signals</Link>
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Select instrument + method</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <select
            value={instrument}
            onChange={(e) => setInstrument(e.target.value)}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
            aria-label="instrument"
          >
            <option value="">Choose instrument…</option>
            {(instruments.data ?? []).map((i) => (
              <option key={i.instrument_id} value={i.instrument_id}>
                {i.instrument_id} — {i.name}
              </option>
            ))}
          </select>
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

      {instrument === "" && (
        <p className="text-sm text-muted-foreground">
          Pick an instrument to see its COT breakdown.
        </p>
      )}

      {instrument && breakdown.isLoading && (
        <p className="text-sm">Loading positioning breakdown…</p>
      )}
      {instrument && breakdown.isError && (
        <p className="text-sm text-destructive">Could not load breakdown.</p>
      )}
      {instrument && breakdown.data && breakdown.data.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No COT data yet</CardTitle>
            <CardDescription>
              The CFTC ingest hasn't populated rows for this instrument /
              report combination yet. Run the positioning ingest in Dagster
              first.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {instrument && breakdown.data && breakdown.data.length > 0 && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Positioning over time</CardTitle>
              <CardDescription>
                Long (green) and short (red) stacked, with net positioning
                overlaid. Short is plotted negative so net = long + short.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div style={{ width: "100%", height: 280 }}>
                <ResponsiveContainer>
                  <AreaChart
                    data={chartData}
                    margin={{ top: 10, right: 20, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="report_ts" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip />
                    <Area
                      type="monotone"
                      dataKey="long"
                      stackId="1"
                      stroke="#22c55e"
                      fill="#22c55e"
                      fillOpacity={0.35}
                    />
                    <Area
                      type="monotone"
                      dataKey="short"
                      stackId="1"
                      stroke="#ef4444"
                      fill="#ef4444"
                      fillOpacity={0.35}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Signal sparkline</CardTitle>
              <CardDescription>
                {methodId} raw_value over the same window.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div style={{ width: "100%", height: 160 }}>
                <ResponsiveContainer>
                  <LineChart
                    data={chartData}
                    margin={{ top: 10, right: 20, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="report_ts" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} domain={[-1, 1]} />
                    <Tooltip />
                    <Line
                      type="monotone"
                      dataKey="raw_value"
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
              <CardTitle>Breakdown rows</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>report_ts</TableHead>
                    <TableHead>long</TableHead>
                    <TableHead>short</TableHead>
                    <TableHead>net</TableHead>
                    <TableHead>raw_value</TableHead>
                    <TableHead>confidence</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(breakdown.data ?? [])
                    .slice(-12)
                    .map((p: PositioningBreakdownPoint) => (
                      <TableRow key={p.report_ts}>
                        <TableCell className="text-xs">
                          {new Date(p.report_ts).toLocaleDateString()}
                        </TableCell>
                        <TableCell className="text-xs">
                          {p.long?.toLocaleString() ?? "—"}
                        </TableCell>
                        <TableCell className="text-xs">
                          {p.short?.toLocaleString() ?? "—"}
                        </TableCell>
                        <TableCell className="text-xs">
                          {p.net?.toLocaleString() ?? "—"}
                        </TableCell>
                        <TableCell className="text-xs">
                          {p.raw_value !== null ? p.raw_value.toFixed(3) : "—"}
                        </TableCell>
                        <TableCell className="text-xs">
                          {p.confidence !== null
                            ? p.confidence.toFixed(2)
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
