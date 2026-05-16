import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiMethods, type ComparisonRow, type MethodRow } from "@/api/client";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const STATUS_COLORS: Record<MethodRow["status"], "default" | "secondary" | "outline" | "destructive"> = {
  development: "outline",
  baseline: "secondary",
  shadow: "outline",
  production: "default",
  deprecated: "destructive",
};

function groupByComponent(rows: MethodRow[]): Record<string, MethodRow[]> {
  const out: Record<string, MethodRow[]> = {};
  for (const r of rows) {
    if (!out[r.component]) out[r.component] = [];
    out[r.component].push(r);
  }
  return out;
}

/**
 * "Shadow differentiation" badge for the Methods page (Stage 4C
 * Phase 2.3). Reads value_correlation_a_b from the most-recent
 * comparator row per component:
 *
 *  - >= 0.95   muted "near identical" (shadow may not provide a
 *              distinct signal)
 *  - 0.5-0.95  green  "differentiated" (the desired range)
 *  - <  0.5    orange "diverged" (worth investigating)
 *
 * If no comparator data exists yet, renders a neutral "no data" pill.
 */
function ShadowDifferentiationBadge({ component }: { component: string }) {
  const q = useQuery({
    queryKey: ["signals.comparisons", component],
    queryFn: () => apiMethods.signalComparisons(component),
  });
  if (q.isLoading)
    return <Badge variant="outline">comparator: loading…</Badge>;
  if (q.isError || !q.data)
    return (
      <Badge variant="outline" title="Could not load comparator data">
        comparator: error
      </Badge>
    );
  if (q.data.length === 0)
    return (
      <Badge variant="outline" title="No comparator runs yet">
        comparator: no data
      </Badge>
    );
  // Most recent run by created_at.
  const latest = [...q.data].sort((a, b) =>
    a.created_at < b.created_at ? 1 : -1,
  )[0] as ComparisonRow;
  const corr = latest.metrics?.value_correlation_a_b;
  if (corr === null || corr === undefined || Number.isNaN(corr))
    return (
      <Badge variant="outline" title={`Latest run: ${latest.comparison_id}`}>
        comparator: corr=—
      </Badge>
    );
  const abs = Math.abs(corr);
  if (abs >= 0.95)
    return (
      <Badge
        variant="secondary"
        title={
          "value_correlation_a_b is near 1.0 — the shadow may not be providing " +
          "a distinct signal vs the baseline. Investigate before promotion."
        }
      >
        near identical (corr={corr.toFixed(2)})
      </Badge>
    );
  if (abs >= 0.5)
    return (
      <Badge
        variant="default"
        title={
          "value_correlation_a_b is in the differentiated range — shadow " +
          "produces a related-but-distinct signal."
        }
      >
        shadow differentiated (corr={corr.toFixed(2)})
      </Badge>
    );
  return (
    <Badge
      variant="destructive"
      title={
        "value_correlation_a_b is below 0.5 — shadow may be too divergent. " +
        "Verify the underlying methodology rather than promoting blindly."
      }
    >
      diverged (corr={corr.toFixed(2)})
    </Badge>
  );
}

export default function Methods() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["methods"],
    queryFn: apiMethods.listMethods,
  });

  const groups = data ? groupByComponent(data) : {};
  const componentNames = Object.keys(groups).sort();

  return (
    <div className="mx-auto max-w-5xl p-8 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Methods registry</h1>
          <p className="text-muted-foreground text-sm">
            Cross-cutting framework for comparing baseline vs enhancement methods.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            Refresh
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link to="/">Home</Link>
          </Button>
        </div>
      </header>

      {isLoading && <p className="text-sm">Loading…</p>}
      {isError && <p className="text-sm text-destructive">Could not load methods.</p>}

      {data && data.length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No methods registered</CardTitle>
            <CardDescription>
              Stage 1 ships only the framework. Methods are registered starting in Stage 2 (data
              quality), Stage 3 (signal library), and onward.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              See <code>docs/methods_framework.md</code> for the lifecycle and promotion gate.
            </p>
          </CardContent>
        </Card>
      )}

      {componentNames.map((component) => (
        <Card key={component}>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <div>
                <CardTitle>{component}</CardTitle>
                <CardDescription>
                  {groups[component].length} method
                  {groups[component].length === 1 ? "" : "s"}
                </CardDescription>
              </div>
              <ShadowDifferentiationBadge component={component} />
            </div>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>method_id</TableHead>
                  <TableHead>name</TableHead>
                  <TableHead>version</TableHead>
                  <TableHead>status</TableHead>
                  <TableHead>created</TableHead>
                  <TableHead>last_change</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {groups[component].map((m) => (
                  <TableRow key={m.method_id}>
                    <TableCell className="font-mono text-xs">{m.method_id}</TableCell>
                    <TableCell>{m.name}</TableCell>
                    <TableCell className="text-xs">{m.version}</TableCell>
                    <TableCell>
                      <Badge variant={STATUS_COLORS[m.status]}>{m.status}</Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(m.created_at).toLocaleDateString()}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {new Date(m.status_changed_at).toLocaleDateString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
