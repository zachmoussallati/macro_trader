/**
 * /signals/vol_surface — Stage 5 follow-up.
 *
 * Layout:
 * - Header: instrument selector (6 covered ETFs).
 * - Top: term-structure line chart (ATM IV vs DTE).
 * - Middle: per-slice table.
 * - Bottom: latest signal_values snapshot for the two methods.
 *
 * 3D surface plot deferred (would need Plotly integration; the
 * Stage 5 dashboard pattern is Recharts-only — see
 * notes/stage_5/tradeoffs.md).
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

const UNIVERSE = ["GLD", "SLV", "USO", "UNG", "DBA", "SPY"];

export default function SignalsVolSurface() {
  const [instrument, setInstrument] = useState<string>(UNIVERSE[0]);

  const slices = useQuery({
    queryKey: ["signals.vol_surface.slices", instrument],
    queryFn: () => apiMethods.volSurfaceSlices(instrument),
    enabled: instrument !== "",
  });
  const term = useQuery({
    queryKey: ["signals.vol_surface.term_structure", instrument],
    queryFn: () => apiMethods.volSurfaceTermStructure(instrument),
    enabled: instrument !== "",
  });

  const termChart = useMemo(
    () =>
      (term.data ?? []).map((p) => ({
        dte: p.dte,
        atm_iv: p.atm_iv,
        expiry: new Date(p.expiry_ts).toLocaleDateString(),
      })),
    [term.data],
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Vol surface</h1>
          <p className="text-sm text-muted-foreground">
            ATM IV term structure + per-slice option chain from yfinance.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/signals">Back to signals</Link>
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Underlying</CardTitle>
          <CardDescription>
            Stage 5 universe: GLD / SLV / USO / UNG / DBA / SPY. Other
            instruments show no surface (yfinance options coverage is
            unreliable outside this set).
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <select
            value={instrument}
            onChange={(e) => setInstrument(e.target.value)}
            className="h-10 rounded-md border border-input bg-background px-3 text-sm"
            aria-label="instrument"
          >
            {UNIVERSE.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
          <Badge variant="outline" className="text-xs">
            data_source: yfinance
          </Badge>
          <Badge variant="outline" className="text-xs text-amber-600">
            historical_backtest_supported: false
          </Badge>
        </CardContent>
      </Card>

      {slices.isLoading && <p className="text-sm">Loading chain snapshot…</p>}
      {slices.isError && (
        <p className="text-sm text-destructive">Could not load chain.</p>
      )}
      {slices.data && slices.data.slices.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No chain data yet</CardTitle>
            <CardDescription>
              Run <code>ingest_options_chains</code> in Dagster to populate
              <code> market_data.options_chains</code>.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {slices.data && slices.data.slices.length > 0 && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Term structure</CardTitle>
              <CardDescription>
                Underlying spot:{" "}
                {slices.data.underlying_price?.toFixed(2) ?? "—"}; snapshot{" "}
                {new Date(slices.data.snapshot_ts).toLocaleString()}.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div style={{ width: "100%", height: 240 }}>
                <ResponsiveContainer>
                  <LineChart
                    data={termChart}
                    margin={{ top: 10, right: 20, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="dte" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip />
                    <Line
                      type="monotone"
                      dataKey="atm_iv"
                      stroke="#3b82f6"
                      dot={true}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Per-slice summary</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>expiry</TableHead>
                    <TableHead>dte</TableHead>
                    <TableHead>n_strikes</TableHead>
                    <TableHead>ATM IV</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {slices.data.slices.map((s) => (
                    <TableRow key={s.expiry_ts}>
                      <TableCell className="text-xs">
                        {new Date(s.expiry_ts).toLocaleDateString()}
                      </TableCell>
                      <TableCell className="text-xs">{s.dte}</TableCell>
                      <TableCell className="text-xs">{s.n_strikes}</TableCell>
                      <TableCell className="text-xs">
                        {s.atm_iv !== null ? s.atm_iv.toFixed(3) : "—"}
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
