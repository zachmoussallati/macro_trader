import { useMemo } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  apiMethods,
  type CalendarEvent,
  type CompositeScoreRow,
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
  const composite = useQuery({
    queryKey: ["composite.scores.home"],
    queryFn: () => apiMethods.compositeScores({ method_id: "composite.linear.v1" }),
  });
  const regime = useQuery({
    queryKey: ["regime.current.home"],
    queryFn: () => apiMethods.regimeCurrent("regime.rules.v1"),
  });
  const transition = useQuery({
    queryKey: ["composite.transition.home"],
    queryFn: () => apiMethods.compositeTransitionMultiplier({}),
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

  // Prefer the Stage 7 composite_scores endpoint when populated; fall
  // back to the cross-component heatmap calculation for pre-Stage-7
  // deploys (so Home still renders something useful before the
  // composite job runs).
  const composites = useMemo(() => {
    if (composite.data && composite.data.length > 0) {
      return composite.data
        .filter((c: CompositeScoreRow) => c.score !== null)
        .map((c: CompositeScoreRow) => ({
          instrument_id: c.instrument_id,
          score: c.score as number,
        }))
        .sort((a, b) => b.score - a.score);
    }
    const by_inst: Record<string, number> = {};
    for (const c of heatmap.data ?? []) {
      if (c.zscore === null || c.confidence === null) continue;
      by_inst[c.instrument_id] =
        (by_inst[c.instrument_id] ?? 0) + c.zscore * c.confidence;
    }
    return Object.entries(by_inst)
      .map(([instrument_id, score]) => ({ instrument_id, score }))
      .sort((a, b) => b.score - a.score);
  }, [composite.data, heatmap.data]);
  const topLong = composites.slice(0, 5);
  const topShort = composites.slice(-5).reverse();
  const compositeSource =
    composite.data && composite.data.length > 0
      ? "composite.linear.v1"
      : "heatmap fallback";

  return (
    <div className="mx-auto max-w-5xl p-8 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-4xl font-semibold">Macro Trader</h1>
          <p className="text-muted-foreground">
            Stage 7 — Composite scoring + regime conditioning
          </p>
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

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="md:col-span-2">
          <CardHeader>
            <CardTitle>Top ideas</CardTitle>
            <CardDescription>
              Source: <code className="text-xs">{compositeSource}</code>. Falls
              back to per-instrument (z-score × confidence) sum when the
              composite job hasn&apos;t run yet.
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
                      Run composite_score_job (daily 23:45 UTC) to populate.
                    </li>
                  )}
                  {topLong.map(({ instrument_id, score }) => (
                    <li
                      key={instrument_id}
                      className="flex justify-between font-mono text-xs"
                    >
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
                    <li
                      key={instrument_id}
                      className="flex justify-between font-mono text-xs"
                    >
                      <span>{instrument_id}</span>
                      <span>{score.toFixed(2)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="mt-3 flex gap-2">
              <Button asChild variant="outline" size="sm">
                <Link to="/composite">Open /composite →</Link>
              </Button>
              <Button asChild variant="ghost" size="sm">
                <Link to="/signals">/signals heatmap →</Link>
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Regime + conviction</CardTitle>
            <CardDescription>
              Latest from <code className="text-xs">regime.rules.v1</code>.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {regime.data ? (
              <div className="space-y-1">
                <Badge variant="default">{regime.data.label}</Badge>
                <p className="text-xs text-muted-foreground">
                  {regime.data.days_in_regime !== null
                    ? `${regime.data.days_in_regime} days in regime`
                    : "days-in-regime unknown"}
                </p>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                No regime state yet — run regime_classification.
              </p>
            )}
            {transition.data && (
              <div className="space-y-1">
                <p className="text-xs">
                  conviction multiplier{" "}
                  <span className="font-mono">
                    {transition.data.multiplier.toFixed(2)}
                  </span>
                </p>
                {transition.data.changepoint_probability !== null && (
                  <p className="text-xs text-muted-foreground">
                    cp {transition.data.changepoint_probability.toFixed(3)}
                  </p>
                )}
              </div>
            )}
            <Button asChild variant="outline" size="sm">
              <Link to="/regime">Open /regime →</Link>
            </Button>
          </CardContent>
        </Card>
      </div>

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
