import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { apiMethods, type HealthResponse } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

export default function Home() {
  const { data, isLoading, isError } = useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: apiMethods.health,
    refetchInterval: 10_000,
  });
  const { email, logout } = useAuthStore();

  return (
    <div className="mx-auto max-w-4xl p-8 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-4xl font-semibold">Macro Trader</h1>
          <p className="text-muted-foreground">Stage 1 — Foundation</p>
        </div>
        <div className="flex items-center gap-3">
          {email ? (
            <>
              <span className="text-sm text-muted-foreground">{email}</span>
              <Button variant="outline" size="sm" onClick={() => logout()}>
                Log out
              </Button>
            </>
          ) : (
            <Button asChild size="sm">
              <Link to="/login">Log in</Link>
            </Button>
          )}
        </div>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Service health</CardTitle>
          <CardDescription>Polled every 10 seconds.</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading && <p className="text-sm">Loading…</p>}
          {isError && <p className="text-sm text-destructive">Could not reach the API.</p>}
          {data && (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <Badge variant={data.healthy ? "default" : "destructive"}>
                  {data.healthy ? "healthy" : "degraded"}
                </Badge>
                <code className="text-xs text-muted-foreground">v{data.version}</code>
              </div>
              <ul className="space-y-1 text-sm">
                {Object.entries(data.checks).map(([k, v]) => (
                  <li key={k} className="flex items-center gap-2">
                    <Badge variant={v.ok ? "secondary" : "destructive"}>{v.ok ? "ok" : "fail"}</Badge>
                    <code className="text-xs">{k}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Methods framework</CardTitle>
          <CardDescription>
            Baseline vs enhancement methods registered across pipeline components.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild variant="outline">
            <Link to="/methods">Open methods page →</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
