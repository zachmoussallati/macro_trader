import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiMethods, type MethodRow } from "@/api/client";
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
            <CardTitle>{component}</CardTitle>
            <CardDescription>
              {groups[component].length} method{groups[component].length === 1 ? "" : "s"}
            </CardDescription>
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
