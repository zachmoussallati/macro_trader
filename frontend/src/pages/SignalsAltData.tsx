/**
 * /signals/alt_data — Stage 5 follow-up.
 *
 * Layout:
 * - Header: method filter (all / EIA / USDA / Google Trends).
 * - Top: per-instrument breakdown by sub-signal (table).
 *
 * No comparator here by design (Stage 5 prompt + decisions.md §7):
 * each alt-data method targets a different sub-universe so cross-
 * method comparison isn't meaningful.
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

const METHOD_LABELS: Record<string, string> = {
  "alt_data.eia_storage.v1": "EIA storage",
  "alt_data.usda_wasde.v1": "USDA WASDE",
  "alt_data.google_trends.v1": "Google Trends",
};

const FILTERS: Array<{ key: string; label: string }> = [
  { key: "all", label: "All" },
  { key: "alt_data.eia_storage.v1", label: "EIA only" },
  { key: "alt_data.usda_wasde.v1", label: "USDA only" },
  { key: "alt_data.google_trends.v1", label: "Trends only" },
];

export default function SignalsAltData() {
  const [filter, setFilter] = useState<string>("all");

  const components = useQuery({
    queryKey: ["signals.alt_data.components"],
    queryFn: apiMethods.altDataComponents,
  });

  const visibleRows = useMemo(() => {
    if (!components.data) return [];
    if (filter === "all") return components.data;
    return components.data.filter((r) => r.method_id === filter);
  }, [components.data, filter]);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Alt data</h1>
          <p className="text-sm text-muted-foreground">
            Per-instrument breakdown across three alt-data sub-signals.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/signals">Back to signals</Link>
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Filter</CardTitle>
          <CardDescription>
            Each method targets a different commodity subset; uncovered
            instruments emit confidence=0 and are flagged below.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={
                "rounded border px-3 py-1 text-xs " +
                (filter === f.key
                  ? "bg-primary text-primary-foreground"
                  : "hover:bg-accent")
              }
            >
              {f.label}
            </button>
          ))}
        </CardContent>
      </Card>

      {components.isLoading && <p className="text-sm">Loading components…</p>}
      {components.isError && (
        <p className="text-sm text-destructive">Could not load components.</p>
      )}
      {visibleRows.length === 0 && !components.isLoading && (
        <Card>
          <CardHeader>
            <CardTitle>No alt-data rows yet</CardTitle>
            <CardDescription>
              Run <code>signal_alt_data</code> in Dagster to populate.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {visibleRows.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Per-instrument breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>instrument</TableHead>
                  <TableHead>method</TableHead>
                  <TableHead>raw_value</TableHead>
                  <TableHead>confidence</TableHead>
                  <TableHead>covered</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visibleRows.map((r) => (
                  <TableRow key={`${r.instrument_id}|${r.method_id}`}>
                    <TableCell className="font-mono text-xs">
                      {r.instrument_id}
                    </TableCell>
                    <TableCell className="text-xs">
                      {METHOD_LABELS[r.method_id] ?? r.method_id}
                    </TableCell>
                    <TableCell className="text-xs">
                      {r.raw_value !== null ? r.raw_value.toFixed(3) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {r.confidence !== null ? r.confidence.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      <Badge variant={r.covered ? "default" : "outline"}>
                        {r.covered ? "covered" : "n/a"}
                      </Badge>
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
