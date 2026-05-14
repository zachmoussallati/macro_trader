import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  apiMethods,
  type CalendarEvent,
  type Freshness,
  type HealthResponse,
} from "@/api/client";
import { useAuthStore } from "@/stores/auth";

export default function Home() {
  const { data: health, isLoading, isError } = useQuery<HealthResponse>({
    queryKey: ["health"],
    queryFn: apiMethods.health,
    refetchInterval: 10_000,
  });
  const freshness = useQuery({
    queryKey: ["data.freshness"],
    queryFn: apiMethods.listFreshness,
  });
  const qualitySummary = useQuery({
    queryKey: ["data.quality.summary"],
    queryFn: () => apiMethods.qualitySummary(24),
  });
  const events = useQuery({
    queryKey: ["calendar.upcoming"],
    queryFn: () => {
      const from = new Date().toISOString();
      const to = new Date(Date.now() + 14 * 86_400_000).toISOString();
      return apiMethods.listEvents({ from, to, importance: ["high"] });
    },
  });
  const heatmap = useQuery({
    queryKey: ["signals.heatmap.home"],
    queryFn: apiMethods.signalHeatmap,
  });

  const { email, logout } = useAuthStore();

  const staleCount =
    freshness.data?.filter((f: Freshness) => f.is_stale).length ?? 0;
  const totalFreshness = freshness.data?.length ?? 0;
  const totalFlags24h = Object.values(qualitySummary.data ?? {}).reduce(
    (a: number, b) => a + (b as number),
    0,
  );
  const topEvents = (events.data ?? []).slice(0, 5);

  // Cross-component composite per instrument: sum of (zscore * confidence)
  // across components. Rank to find top 5 long + top 5 short.
  const composites = useMemo(() => {
    const by_inst: Record<string, number> = {};
    for (const c of heatmap.data ?? []) {
      if (c.zscore === null || c.confidence === null) continue;
      by_inst[c.instrument_id] =
        (by_inst[c.instrument_id] ?? 0) + c.zscore * c.confidence;
    }
    return Object.entries(by_inst)
      .map(([instrument_id, score]) => ({ instrument_id, score }))
      .sort((a, b) => b.score - a.score);
  }, [heatmap.data]);
  const topLong = composites.slice(0, 5);
  const topShort = composites.slice(-5).reverse();

  return (
    <div className="mx-auto max-w-5xl p-8 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-4xl font-semibold">Macro Trader</h1>
          <p className="text-muted-foreground">Stage 3 — Signal Library Part 1</p>
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
          {health && (
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <Badge variant={health.healthy ? "default" : "destructive"}>
                  {health.healthy ? "healthy" : "degraded"}
                </Badge>
                <code className="text-xs text-muted-foreground">v{health.version}</code>
              </div>
              <ul className="space-y-1 text-sm">
                {Object.entries(health.checks).map(([k, v]) => (
                  <li key={k} className="flex items-center gap-2">
                    <Badge variant={v.ok ? "secondary" : "destructive"}>
                      {v.ok ? "ok" : "fail"}
                    </Badge>
                    <code className="text-xs">{k}</code>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Data freshness</CardTitle>
            <CardDescription>
              {staleCount === 0
                ? "All sources up to date."
                : `${staleCount}/${totalFreshness} sources stale`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline" size="sm">
              <Link to="/data">Open /data →</Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Data quality</CardTitle>
            <CardDescription>{totalFlags24h} flags in the last 24h</CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline" size="sm">
              <Link to="/data">Open /data →</Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Calendar</CardTitle>
            <CardDescription>
              {topEvents.length === 0
                ? "No high-importance events in the next 14 days."
                : `Next ${topEvents.length} high-importance events:`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1 text-xs">
              {topEvents.map((e: CalendarEvent) => (
                <li key={e.event_id}>
                  <span className="text-muted-foreground">
                    {new Date(e.event_ts).toLocaleDateString(undefined, {
                      month: "short",
                      day: "numeric",
                    })}
                  </span>{" "}
                  {e.subject}
                </li>
              ))}
            </ul>
            <div className="mt-3">
              <Button asChild variant="outline" size="sm">
                <Link to="/calendar">Open /calendar →</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Top signals</CardTitle>
          <CardDescription>
            Cross-component composite: sum of (z-score × confidence) per instrument across
            trend, carry, and value.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
                Top 5 long
              </h4>
              <ul className="space-y-1 text-sm">
                {topLong.length === 0 && (
                  <li className="text-xs text-muted-foreground">
                    Run compute_all_signals_job to populate.
                  </li>
                )}
                {topLong.map(({ instrument_id, score }) => (
                  <li key={instrument_id} className="flex justify-between font-mono text-xs">
                    <span>{instrument_id}</span>
                    <span>{score.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="text-xs font-semibold uppercase text-muted-foreground mb-2">
                Top 5 short
              </h4>
              <ul className="space-y-1 text-sm">
                {topShort.map(({ instrument_id, score }) => (
                  <li key={instrument_id} className="flex justify-between font-mono text-xs">
                    <span>{instrument_id}</span>
                    <span>{score.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <div className="mt-3">
            <Button asChild variant="outline" size="sm">
              <Link to="/signals">Open /signals →</Link>
            </Button>
          </div>
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
