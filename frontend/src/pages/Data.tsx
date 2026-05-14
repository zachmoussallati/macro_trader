import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  apiMethods,
  type DataSource,
  type Freshness,
  type InstrumentRow,
  type QualityFlag,
  type SeriesRow,
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

type Tab = "sources" | "series" | "instruments" | "quality";

const TABS: { key: Tab; label: string }[] = [
  { key: "sources", label: "Sources" },
  { key: "series", label: "Series" },
  { key: "instruments", label: "Instruments" },
  { key: "quality", label: "Quality" },
];

export default function Data() {
  const [tab, setTab] = useState<Tab>("sources");

  return (
    <div className="mx-auto max-w-6xl p-8 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Data layer</h1>
          <p className="text-muted-foreground text-sm">
            Free-source ingestion, point-in-time observations, calendar events, quality flags.
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

      {tab === "sources" && <SourcesTab />}
      {tab === "series" && <SeriesTab />}
      {tab === "instruments" && <InstrumentsTab />}
      {tab === "quality" && <QualityTab />}
    </div>
  );
}

// =====================================================================
// Sources tab
// =====================================================================
function SourcesTab() {
  const sources = useQuery({ queryKey: ["data.sources"], queryFn: apiMethods.listSources });
  const freshness = useQuery({
    queryKey: ["data.freshness"],
    queryFn: apiMethods.listFreshness,
  });

  if (sources.isLoading || freshness.isLoading) return <p className="text-sm">Loading…</p>;
  if (sources.isError || freshness.isError)
    return <p className="text-sm text-destructive">Could not load sources.</p>;

  const freshnessBySource = new Map<string, Freshness>();
  for (const f of freshness.data ?? []) {
    if (!freshnessBySource.has(f.source_id)) freshnessBySource.set(f.source_id, f);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Data sources</CardTitle>
        <CardDescription>Health + most-recent freshness per source.</CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>source_id</TableHead>
              <TableHead>name</TableHead>
              <TableHead>health</TableHead>
              <TableHead>last success</TableHead>
              <TableHead>stale</TableHead>
              <TableHead>auth</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(sources.data ?? []).map((s: DataSource) => {
              const f = freshnessBySource.get(s.source_id);
              return (
                <TableRow key={s.source_id}>
                  <TableCell className="font-mono text-xs">{s.source_id}</TableCell>
                  <TableCell>{s.name}</TableCell>
                  <TableCell>
                    <Badge variant={s.is_healthy ? "default" : "destructive"}>
                      {s.is_healthy ? "healthy" : "down"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {f?.last_successful_at
                      ? new Date(f.last_successful_at).toLocaleString()
                      : "—"}
                  </TableCell>
                  <TableCell>
                    {f?.is_stale ? (
                      <Badge variant="destructive">stale</Badge>
                    ) : (
                      <Badge variant="secondary">fresh</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-xs">{s.requires_auth ? "yes" : "no"}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// =====================================================================
// Series tab
// =====================================================================
function SeriesTab() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["data.series"],
    queryFn: apiMethods.listSeries,
  });
  if (isLoading) return <p className="text-sm">Loading…</p>;
  if (isError) return <p className="text-sm text-destructive">Could not load series.</p>;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Macro series</CardTitle>
        <CardDescription>
          {data?.length ?? 0} active series. Click a series_id to inspect (Stage 3).
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>series_id</TableHead>
              <TableHead>name</TableHead>
              <TableHead>source</TableHead>
              <TableHead>frequency</TableHead>
              <TableHead>category</TableHead>
              <TableHead>affects</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(data ?? []).map((s: SeriesRow) => (
              <TableRow key={s.series_id}>
                <TableCell className="font-mono text-xs">{s.series_id}</TableCell>
                <TableCell>{s.name}</TableCell>
                <TableCell className="text-xs">{s.source}</TableCell>
                <TableCell className="text-xs">{s.frequency}</TableCell>
                <TableCell className="text-xs">{s.category ?? "—"}</TableCell>
                <TableCell className="text-xs">{s.affected_instruments.join(", ") || "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// =====================================================================
// Instruments tab
// =====================================================================
function InstrumentsTab() {
  const instruments = useQuery({
    queryKey: ["data.instruments"],
    queryFn: apiMethods.listInstruments,
  });
  if (instruments.isLoading) return <p className="text-sm">Loading…</p>;
  if (instruments.isError)
    return <p className="text-sm text-destructive">Could not load instruments.</p>;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Instruments</CardTitle>
        <CardDescription>13 commodity futures via ETF proxies.</CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>symbol</TableHead>
              <TableHead>name</TableHead>
              <TableHead>asset class</TableHead>
              <TableHead>proxy</TableHead>
              <TableHead>exchange</TableHead>
              <TableHead>tracking notes</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(instruments.data ?? []).map((row: InstrumentRow) => (
              <TableRow key={row.instrument_id}>
                <TableCell className="font-mono text-xs">{row.instrument_id}</TableCell>
                <TableCell>{row.name}</TableCell>
                <TableCell className="text-xs">{row.asset_class}</TableCell>
                <TableCell className="text-xs">{row.proxy_ticker ?? "—"}</TableCell>
                <TableCell className="text-xs">{row.exchange ?? "—"}</TableCell>
                <TableCell className="text-xs text-muted-foreground max-w-md">
                  {row.tracking_error_notes ?? "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// =====================================================================
// Quality tab
// =====================================================================
function QualityTab() {
  const flags = useQuery({
    queryKey: ["data.quality.flags"],
    queryFn: () => apiMethods.listQualityFlags(7),
  });
  const summary = useQuery({
    queryKey: ["data.quality.summary"],
    queryFn: () => apiMethods.qualitySummary(24),
  });

  if (flags.isLoading || summary.isLoading) return <p className="text-sm">Loading…</p>;

  const grouped: Record<string, QualityFlag[]> = {};
  for (const f of flags.data ?? []) {
    (grouped[f.method_id] ||= []).push(f);
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Flag counts (last 24h)</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-1 text-sm">
            {Object.entries(summary.data ?? {}).map(([method, count]) => (
              <li key={method}>
                <code className="text-xs">{method}</code>:{" "}
                <Badge variant="secondary">{count}</Badge>
              </li>
            ))}
            {Object.keys(summary.data ?? {}).length === 0 && (
              <li className="text-muted-foreground">No flags in the last 24 hours.</li>
            )}
          </ul>
        </CardContent>
      </Card>

      {Object.entries(grouped).map(([method, methodFlags]) => (
        <Card key={method}>
          <CardHeader>
            <CardTitle className="font-mono text-base">{method}</CardTitle>
            <CardDescription>
              {methodFlags.length} flag{methodFlags.length === 1 ? "" : "s"} in the last 7 days
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>series</TableHead>
                  <TableHead>value_ts</TableHead>
                  <TableHead>value</TableHead>
                  <TableHead>run_at</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {methodFlags.slice(0, 25).map((f) => (
                  <TableRow key={f.flag_id}>
                    <TableCell className="font-mono text-xs">{f.series_id}</TableCell>
                    <TableCell className="text-xs">
                      {new Date(f.value_ts).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-xs">
                      {f.value !== null ? f.value.toFixed(4) : "—"}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(f.run_at).toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ))}

      {Object.keys(grouped).length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No flags yet</CardTitle>
            <CardDescription>
              The daily quality job populates this. Trigger it manually in Dagster or wait for the
              23:00 UTC schedule.
            </CardDescription>
          </CardHeader>
        </Card>
      )}
    </div>
  );
}
