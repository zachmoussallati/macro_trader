/**
 * /signals/factor_exposure — factor exposure inspector (Stage 4C).
 *
 * Layout:
 * - Header: method selector (OLS / RF / Causal Forest).
 * - Top: current factor z-scores (six factors).
 * - Middle: per-instrument loadings + signal snapshot.
 * - Bottom: factor contribution breakdown for a chosen instrument
 *           (clicked from the loadings table).
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
import { apiMethods, ApiError } from "@/api/client";
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
  { id: "factor_exposure.ols.v1", label: "OLS (baseline)" },
  { id: "factor_exposure.rf.v1", label: "Random Forest" },
  {
    id: "factor_exposure.causal_forest.v1",
    label: "Causal Forest (needs [ml] extra)",
  },
];

const FACTORS = ["growth", "inflation", "liquidity", "usd", "oil", "risk_on"];

export default function SignalsFactorExposure() {
  const [methodId, setMethodId] = useState<string>(METHODS[0].id);
  const [selectedInstrument, setSelectedInstrument] = useState<string | null>(
    null,
  );

  const factors = useQuery({
    queryKey: ["signals.factor_exposure.factors"],
    queryFn: apiMethods.factorExposureFactors,
  });
  const loadings = useQuery({
    queryKey: ["signals.factor_exposure.loadings", methodId],
    queryFn: () => apiMethods.factorExposureLoadings(methodId),
  });
  const contributions = useQuery({
    queryKey: [
      "signals.factor_exposure.contributions",
      methodId,
      selectedInstrument,
    ],
    queryFn: () =>
      apiMethods.factorExposureContributions(
        selectedInstrument as string,
        methodId,
      ),
    enabled: selectedInstrument !== null,
  });

  // Detect "method not available" — Causal Forest specifically may
  // return an empty list when the [ml] extra isn't installed.
  const cfNotAvailable =
    methodId === "factor_exposure.causal_forest.v1" &&
    !loadings.isLoading &&
    (loadings.data ?? []).length === 0;

  const contributionChart = useMemo(() => {
    if (!contributions.data) return [];
    return contributions.data.contributions.map((c) => ({
      factor: c.factor_name,
      contribution: c.contribution,
    }));
  }, [contributions.data]);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold">Factor exposure</h1>
          <p className="text-sm text-muted-foreground">
            Per-instrument exposure to six macro factors; composite signal =
            −β · z_today.
          </p>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to="/signals">Back to signals</Link>
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Method</CardTitle>
        </CardHeader>
        <CardContent>
          <select
            value={methodId}
            onChange={(e) => {
              setMethodId(e.target.value);
              setSelectedInstrument(null);
            }}
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
          <CardTitle>Macro factor z-scores (today)</CardTitle>
          <CardDescription>
            6-factor view: growth / inflation / liquidity / usd / oil /
            risk_on. Reads from `signals.factor_exposure.factors`.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {factors.isLoading && <p className="text-sm">Loading…</p>}
          {factors.isError && (
            <p className="text-sm text-destructive">Could not load factors.</p>
          )}
          {factors.data && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>factor</TableHead>
                  <TableHead>z-score</TableHead>
                  <TableHead>FRED series</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {factors.data.map((f) => (
                  <TableRow key={f.factor_name}>
                    <TableCell className="font-mono text-xs">
                      {f.factor_name}
                    </TableCell>
                    <TableCell className="text-xs">
                      {f.zscore !== null ? f.zscore.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {f.fred_series}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {cfNotAvailable && (
        <Card>
          <CardHeader>
            <CardTitle>Causal Forest unavailable</CardTitle>
            <CardDescription>
              The optional <code>[ml]</code> extra (EconML) isn't installed,
              so <code>factor_exposure.causal_forest.v1</code> isn't
              registered. Install via <code>uv sync --extra ml</code> to
              enable.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      {!cfNotAvailable && loadings.data && loadings.data.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Per-instrument loadings + signal</CardTitle>
            <CardDescription>
              Click a row to see the per-factor contribution breakdown.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>instrument</TableHead>
                  {FACTORS.map((f) => (
                    <TableHead key={f}>{f}</TableHead>
                  ))}
                  <TableHead>raw</TableHead>
                  <TableHead>rank</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loadings.data.map((l) => (
                  <TableRow
                    key={l.instrument_id}
                    onClick={() => setSelectedInstrument(l.instrument_id)}
                    className={
                      "cursor-pointer hover:bg-muted/50 " +
                      (selectedInstrument === l.instrument_id ? "bg-muted" : "")
                    }
                  >
                    <TableCell className="font-mono text-xs">
                      {l.instrument_id}
                    </TableCell>
                    {FACTORS.map((f) => (
                      <TableCell key={f} className="text-xs">
                        {l.factor_loadings[f] !== undefined
                          ? l.factor_loadings[f].toFixed(3)
                          : "—"}
                      </TableCell>
                    ))}
                    <TableCell className="text-xs">
                      {l.raw_value !== null ? l.raw_value.toFixed(3) : "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {l.rank !== null ? l.rank.toFixed(2) : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {selectedInstrument && (
        <Card>
          <CardHeader>
            <CardTitle>
              {selectedInstrument} — factor contribution breakdown
            </CardTitle>
            <CardDescription>
              Contribution = −loading × z_today per factor. Sum equals the
              stored pre-tanh raw score.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {contributions.isLoading && (
              <p className="text-sm">Loading contributions…</p>
            )}
            {contributions.isError && (
              <p className="text-sm text-destructive">
                {contributions.error instanceof ApiError &&
                contributions.error.status === 404
                  ? "No factor exposure signal value yet for this instrument."
                  : "Could not load contributions."}
              </p>
            )}
            {contributions.data && (
              <div style={{ width: "100%", height: 220 }}>
                <ResponsiveContainer>
                  <BarChart
                    data={contributionChart}
                    margin={{ top: 10, right: 20, left: 0, bottom: 0 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="factor" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip />
                    <Bar dataKey="contribution" fill="#3b82f6" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
