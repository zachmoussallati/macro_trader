/**
 * /signals/catalyst — catalyst calendar view (Stage 4C).
 *
 * Layout:
 * - Header: days-ahead selector, method selector.
 * - Top: upcoming events table (date, subject, kind, importance,
 *        affected instruments).
 * - Middle: catalyst pressure bar chart (per-instrument forward score).
 * - Bottom: per-instrument historical sensitivity table (when an
 *           instrument is selected from the pressure chart).
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
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
  { id: "catalyst.event_study.v1", label: "Event-study (baseline)" },
  {
    id: "catalyst.causal.v1",
    label: "Causal (placeholder until Phase 2 lands)",
  },
];

export default function SignalsCatalyst() {
  const [daysAhead, setDaysAhead] = useState<number>(10);
  const [methodId, setMethodId] = useState<string>(METHODS[0].id);
  const [selectedInstrument, setSelectedInstrument] = useState<string | null>(
    null,
  );

  const events = useQuery({
    queryKey: ["signals.catalyst.events", daysAhead],
    queryFn: () => apiMethods.catalystEvents({ days_ahead: daysAhead }),
  });
  const pressure = useQuery({
    queryKey: ["signals.catalyst.pressure", methodId],
    queryFn: () => apiMethods.catalystPressure(methodId),
  });
  const historical = useQuery({
    queryKey: ["signals.catalyst.historical", selectedInstrument],
    queryFn: () =>
      apiMethods.catalystHistorical(selectedInstrument as string, {
        lookback_years: 5,
      }),
    enabled: selectedInstrument !== null,
  });

  const pressureChart = useMemo(
    () =>
      (pressure.data ?? []).map((p) => ({
        instrument_id: p.instrument_id,
        pressure: p.raw_value ?? 0,
      })),
    [pressure.data],
  );

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Catalyst</h1>
          <p className="text-sm text-muted-foreground">
            Upcoming events + per-instrument forward pressure scores.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/signals">Back to signals</Link>
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Controls</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <label className="text-xs text-muted-foreground">
            Days ahead:&nbsp;
            <input
              type="number"
              min={1}
              max={60}
              value={daysAhead}
              onChange={(e) => setDaysAhead(Number(e.target.value))}
              className="ml-1 h-9 w-20 rounded-md border border-input bg-background px-2 text-sm"
              aria-label="days ahead"
            />
          </label>
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

      <Card>
        <CardHeader>
          <CardTitle>Upcoming events</CardTitle>
          <CardDescription>
            From `calendar_events` filtered to medium / high importance.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {events.isLoading && <p className="text-sm">Loading…</p>}
          {events.isError && (
            <p className="text-sm text-destructive">Could not load events.</p>
          )}
          {events.data && events.data.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No events in this window.
            </p>
          )}
          {events.data && events.data.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>when</TableHead>
                  <TableHead>kind</TableHead>
                  <TableHead>subject</TableHead>
                  <TableHead>importance</TableHead>
                  <TableHead>affects</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {events.data.map((e) => (
                  <TableRow key={`${e.event_ts}-${e.subject}`}>
                    <TableCell className="text-xs">
                      {new Date(e.event_ts).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-xs">{e.kind}</TableCell>
                    <TableCell className="text-xs">{e.subject}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className="text-xs">
                        {e.importance}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {e.affected_instruments.join(", ") || "—"}
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
          <CardTitle>Catalyst pressure by instrument</CardTitle>
          <CardDescription>
            Time-decayed sum of upcoming-event sensitivities. Click an
            instrument to see its historical event-by-event returns.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {pressure.isLoading && <p className="text-sm">Loading…</p>}
          {pressure.isError && (
            <p className="text-sm text-destructive">
              Could not load catalyst pressure.
            </p>
          )}
          {pressure.data && pressure.data.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No catalyst signal values yet — run the signal_catalyst Dagster
              asset.
            </p>
          )}
          {pressure.data && pressure.data.length > 0 && (
            <>
              <div style={{ width: "100%", height: 280 }}>
                <ResponsiveContainer>
                  <BarChart
                    data={pressureChart}
                    margin={{ top: 10, right: 20, left: 0, bottom: 0 }}
                    onClick={(state) => {
                      const inst = state?.activeLabel as string | undefined;
                      if (inst) setSelectedInstrument(inst);
                    }}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="instrument_id" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 10 }} domain={[-1, 1]} />
                    <Tooltip />
                    <Bar dataKey="pressure" fill="#f59e0b" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                Selected: {selectedInstrument ?? "(click a bar)"}
              </p>
            </>
          )}
        </CardContent>
      </Card>

      {selectedInstrument && (
        <Card>
          <CardHeader>
            <CardTitle>
              {selectedInstrument} — historical event returns
            </CardTitle>
            <CardDescription>
              5-year lookback of event-by-event abnormal log-returns in
              window [-1, +1] days.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {historical.isLoading && <p className="text-sm">Loading…</p>}
            {historical.isError && (
              <p className="text-sm text-destructive">
                Could not load historical sensitivities.
              </p>
            )}
            {historical.data && historical.data.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No historical events for this instrument in the lookback.
              </p>
            )}
            {historical.data && historical.data.length > 0 && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>event_ts</TableHead>
                    <TableHead>subject</TableHead>
                    <TableHead>log_return</TableHead>
                    <TableHead>|return|</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {historical.data.slice(-30).map((h) => (
                    <TableRow
                      key={`${h.event_ts}-${h.subject}`}
                      className={h.log_return < 0 ? "text-destructive" : ""}
                    >
                      <TableCell className="text-xs">
                        {new Date(h.event_ts).toLocaleDateString()}
                      </TableCell>
                      <TableCell className="text-xs">{h.subject}</TableCell>
                      <TableCell className="text-xs">
                        {h.log_return.toFixed(4)}
                      </TableCell>
                      <TableCell className="text-xs">
                        {h.abs_return.toFixed(4)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
