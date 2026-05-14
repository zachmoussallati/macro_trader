/**
 * Thin fetch wrapper with JWT support.
 * - Reads bearer token from the auth store.
 * - Throws ApiError on non-2xx.
 */

import { useAuthStore } from "@/stores/auth";

export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000/api/v1";

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`API ${status}: ${typeof body === "string" ? body : JSON.stringify(body)}`);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = useAuthStore.getState().accessToken;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const res = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new ApiError(res.status, body);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T>(p: string) => request<T>(p, { method: "GET" }),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
};

// ---------------- Domain shapes ----------------
export interface HealthResponse {
  service: string;
  version: string;
  now: string;
  healthy: boolean;
  checks: Record<string, { ok: boolean; [k: string]: unknown }>;
}

export interface MethodRow {
  method_id: string;
  component: string;
  name: string;
  version: string;
  status: "development" | "baseline" | "shadow" | "production" | "deprecated";
  metadata: Record<string, unknown>;
  created_at: string;
  status_changed_at: string;
  status_reason: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserOut {
  id: string;
  email: string;
  role: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
}

// ---------------- Stage 2: data + calendar shapes ----------------
export interface DataSource {
  source_id: string;
  name: string;
  base_url: string | null;
  requires_auth: boolean;
  is_healthy: boolean;
  last_health_check: string | null;
  rate_limit_notes: string | null;
}

export interface Freshness {
  source_id: string;
  series_or_table: string;
  expected_frequency: string;
  last_successful_at: string | null;
  last_attempted_at: string | null;
  is_stale: boolean;
  consecutive_failures: number;
}

export interface Lineage {
  lineage_id: string;
  source_id: string;
  fetched_at: string;
  fetch_method: string | null;
  rows_ingested: number | null;
  rows_updated: number | null;
  rows_rejected: number | null;
  error_count: number;
  dagster_run_id: string | null;
  dagster_asset_key: string | null;
}

export interface SeriesRow {
  series_id: string;
  name: string;
  source: string;
  frequency: string;
  units: string | null;
  category: string | null;
  affected_instruments: string[];
  is_active: boolean;
}

export interface InstrumentRow {
  instrument_id: string;
  name: string;
  asset_class: string;
  sub_class: string | null;
  proxy_ticker: string | null;
  exchange: string | null;
  is_active: boolean;
  tracking_error_notes: string | null;
}

export interface BarRow {
  instrument_id: string;
  value_ts: string;
  observation_ts: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  adjusted_close: number | null;
}

export interface QualityFlag {
  flag_id: string;
  method_id: string;
  series_id: string;
  value_ts: string;
  value: number | null;
  is_flagged: boolean;
  run_at: string;
}

export interface CalendarEvent {
  event_id: string;
  event_ts: string;
  actual_release_ts: string | null;
  kind: string;
  subject: string;
  region: string | null;
  importance: "low" | "medium" | "high";
  affected_instruments: string[];
  affected_series: string[];
  source: string;
  metadata: Record<string, unknown>;
}

// ---------------- Stage 3: signals shapes ----------------
export interface SignalMeta {
  signal_id: string;
  component: string;
  name: string;
  version: string;
  status: "development" | "baseline" | "shadow" | "production" | "deprecated";
}

export interface SignalValueRow {
  signal_id: string;
  instrument_id: string;
  value_ts: string;
  observation_ts: string;
  raw_value: number | null;
  zscore: number | null;
  rank: number | null;
  confidence: number | null;
  rolling_sharpe_252: number | null;
  metadata: Record<string, unknown>;
}

export interface HeatmapCell {
  instrument_id: string;
  component: string;
  signal_id: string;
  raw_value: number | null;
  zscore: number | null;
  rank: number | null;
  confidence: number | null;
  value_ts: string;
}

export interface DecayPoint {
  value_ts: string;
  rolling_sharpe_252: number | null;
}

export const apiMethods = {
  // ----- core -----
  health: () => api.get<HealthResponse>("/health"),
  listMethods: () => api.get<MethodRow[]>("/methods"),
  login: (email: string, password: string) =>
    api.post<TokenPair>("/auth/login", { email, password }),
  me: () => api.get<UserOut>("/auth/me"),

  // ----- data -----
  listSources: () => api.get<DataSource[]>("/data/sources"),
  listFreshness: () => api.get<Freshness[]>("/data/freshness"),
  listLineage: (source?: string, limit = 50) =>
    api.get<Lineage[]>(
      `/data/lineage?${new URLSearchParams({ ...(source && { source }), limit: String(limit) })}`,
    ),
  listSeries: () => api.get<SeriesRow[]>("/data/series"),
  listInstruments: () => api.get<InstrumentRow[]>("/data/instruments"),
  listBars: (instrumentId: string, limit = 30) =>
    api.get<BarRow[]>(`/data/instruments/${instrumentId}/bars?limit=${limit}`),
  listQualityFlags: (lookbackDays = 7) =>
    api.get<QualityFlag[]>(`/data/quality/flags?lookback_days=${lookbackDays}`),
  qualitySummary: (lookbackHours = 24) =>
    api.get<Record<string, number>>(`/data/quality/summary?lookback_hours=${lookbackHours}`),

  // ----- calendar -----
  listEvents: (params: { from?: string; to?: string; importance?: string[] } = {}) => {
    const q = new URLSearchParams();
    if (params.from) q.set("from", params.from);
    if (params.to) q.set("to", params.to);
    (params.importance ?? []).forEach((i) => q.append("importance", i));
    return api.get<CalendarEvent[]>(`/calendar/events?${q.toString()}`);
  },

  // ----- signals -----
  listSignals: () => api.get<SignalMeta[]>("/signals"),
  signalHeatmap: () => api.get<HeatmapCell[]>("/signals/heatmap"),
  latestSignalValues: (instrument?: string) =>
    api.get<SignalValueRow[]>(
      `/signals/values${instrument ? `?instrument=${encodeURIComponent(instrument)}` : ""}`,
    ),
  signalValues: (
    signalId: string,
    params: { instrument?: string; limit?: number } = {},
  ) => {
    const q = new URLSearchParams();
    if (params.instrument) q.set("instrument", params.instrument);
    if (params.limit) q.set("limit", String(params.limit));
    return api.get<SignalValueRow[]>(
      `/signals/${encodeURIComponent(signalId)}/values?${q.toString()}`,
    );
  },
  signalDecay: (signalId: string, lookbackDays = 365) =>
    api.get<DecayPoint[]>(
      `/signals/${encodeURIComponent(signalId)}/decay?lookback_days=${lookbackDays}`,
    ),
  signalComparisons: (component?: string) =>
    api.get<unknown[]>(
      `/signals/comparisons${component ? `?component=${encodeURIComponent(component)}` : ""}`,
    ),
};
