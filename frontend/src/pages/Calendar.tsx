import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiMethods, type CalendarEvent } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const IMPORTANCE_LEVELS = ["high", "medium", "low"] as const;

const IMPORTANCE_VARIANT: Record<
  CalendarEvent["importance"],
  "default" | "secondary" | "outline"
> = {
  high: "default",
  medium: "secondary",
  low: "outline",
};

export default function Calendar() {
  const [importance, setImportance] = useState<string[]>(["high", "medium"]);
  const now = useMemo(() => new Date(), []);
  const monthStart = useMemo(() => new Date(now.getFullYear(), now.getMonth(), 1), [now]);
  const monthEnd = useMemo(
    () => new Date(now.getFullYear(), now.getMonth() + 2, 0),
    [now],
  );

  const { data, isLoading, isError } = useQuery({
    queryKey: ["calendar.events", importance.join(",")],
    queryFn: () =>
      apiMethods.listEvents({
        from: monthStart.toISOString(),
        to: monthEnd.toISOString(),
        importance,
      }),
  });

  const eventsByDate = useMemo(() => {
    const map: Record<string, CalendarEvent[]> = {};
    for (const e of data ?? []) {
      const key = new Date(e.event_ts).toISOString().slice(0, 10);
      (map[key] ||= []).push(e);
    }
    return map;
  }, [data]);

  return (
    <div className="mx-auto max-w-6xl p-8 space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Calendar</h1>
          <p className="text-muted-foreground text-sm">
            Data releases, central-bank decisions, and other catalysts.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/">Home</Link>
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Filters</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Importance:</span>
            {IMPORTANCE_LEVELS.map((level) => {
              const active = importance.includes(level);
              return (
                <button
                  key={level}
                  onClick={() =>
                    setImportance((prev) =>
                      prev.includes(level) ? prev.filter((p) => p !== level) : [...prev, level],
                    )
                  }
                  className={`px-3 py-1 rounded-md text-xs border transition-colors ${
                    active
                      ? "bg-primary text-primary-foreground border-primary"
                      : "border-input"
                  }`}
                >
                  {level}
                </button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {isLoading && <p className="text-sm">Loading…</p>}
      {isError && <p className="text-sm text-destructive">Could not load events.</p>}

      {!isLoading && (data ?? []).length === 0 && (
        <Card>
          <CardHeader>
            <CardTitle>No events</CardTitle>
            <CardDescription>
              Try widening the importance filter, or refresh the calendar via the
              `refresh_calendar_events` Dagster asset.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      <div className="space-y-3">
        {Object.entries(eventsByDate)
          .sort()
          .map(([date, events]) => (
            <Card key={date}>
              <CardHeader>
                <CardTitle className="text-base">
                  {new Date(date).toLocaleDateString(undefined, {
                    weekday: "long",
                    month: "short",
                    day: "numeric",
                  })}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-2">
                  {events.map((e) => (
                    <li key={e.event_id} className="flex items-start gap-3 text-sm">
                      <Badge variant={IMPORTANCE_VARIANT[e.importance]}>{e.importance}</Badge>
                      <span className="text-xs text-muted-foreground whitespace-nowrap">
                        {new Date(e.event_ts).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                      <span className="flex-1">
                        <span className="font-medium">{e.subject}</span>
                        {e.region && (
                          <span className="text-xs text-muted-foreground"> · {e.region}</span>
                        )}
                        {e.affected_instruments.length > 0 && (
                          <span className="text-xs text-muted-foreground">
                            {" · "}affects {e.affected_instruments.join(", ")}
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          ))}
      </div>
    </div>
  );
}
