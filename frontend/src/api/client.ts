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

export interface ComparisonRow {
  comparison_id: string;
  component: string;
  method_a_id: string;
  method_b_id: string;
  period_start: string;
  period_end: string;
  metrics: Record<string, number | null>;
  agreement: Record<string, number | null>;
  stability: Record<string, number | null>;
  notes: string;
  created_at: string;
}

// ---------------- Stage 4A/4B/4C: per-component shapes ----------------
export interface PositioningCotPoint {
  report_ts: string;
  publication_ts: string;
  report_type: string;
  open_interest: number | null;
  managed_money_long: number | null;
  managed_money_short: number | null;
  managed_money_net: number | null;
  producer_long: number | null;
  producer_short: number | null;
  producer_net: number | null;
  swap_long: number | null;
  swap_short: number | null;
  nonreportable_long: number | null;
  nonreportable_short: number | null;
}

export interface PositioningBreakdownPoint {
  report_ts: string;
  publication_ts: string;
  long: number | null;
  short: number | null;
  net: number | null;
  open_interest: number | null;
  raw_value: number | null;
  zscore: number | null;
  confidence: number | null;
}

export interface DislocationFactor {
  instrument_id: string;
  method_id: string;
  value_ts: string;
  raw_value: number | null;
  zscore: number | null;
  rank: number | null;
  confidence: number | null;
  explained_variance: number | null;
}

export interface DislocationExplainedVariancePoint {
  value_ts: string;
  explained_variance: number | null;
}

export interface FactorZScore {
  factor_name: string;
  zscore: number | null;
  fred_series: string;
}

export interface FactorExposureLoading {
  instrument_id: string;
  method_id: string;
  factor_loadings: Record<string, number>;
  raw_value: number | null;
  rank: number | null;
  confidence: number | null;
  value_ts: string;
}

export interface FactorContributionRow {
  factor_name: string;
  loading: number;
  zscore: number | null;
  contribution: number;
}

export interface FactorContributions {
  instrument_id: string;
  method_id: string;
  raw_value: number | null;
  sum_contributions: number;
  contributions: FactorContributionRow[];
}

export interface UpcomingCatalyst {
  event_ts: string;
  subject: string;
  kind: string;
  importance: string;
  affected_instruments: string[];
}

export interface CatalystHistorical {
  event_ts: string;
  subject: string;
  log_return: number;
  abs_return: number;
}

export interface CatalystPressure {
  instrument_id: string;
  pressure_score: number | null;
  n_subjects: number;
  raw_value: number | null;
  rank: number | null;
  confidence: number | null;
  value_ts: string | null;
}

// Stage 5 follow-up: vol_surface / nowcasting / alt_data endpoints
export interface VolSurfaceSlicePoint {
  strike: number;
  moneyness: number | null;
  implied_vol: number | null;
  option_type: string;
  delta: number | null;
  open_interest: number | null;
}

export interface VolSurfaceSlice {
  expiry_ts: string;
  dte: number;
  n_strikes: number;
  atm_iv: number | null;
  points: VolSurfaceSlicePoint[];
}

export interface VolSurfaceSlices {
  instrument_id: string;
  snapshot_ts: string;
  underlying_price: number | null;
  slices: VolSurfaceSlice[];
}

export interface VolSurfaceTermStructurePoint {
  expiry_ts: string;
  dte: number;
  atm_iv: number | null;
}

export interface NowcastingProjection {
  release_id: string;
  target_fred: string;
  name: string;
  method_id: string;
  pred_mean: number | null;
  pred_var: number | null;
  last_actual: number | null;
  surprise_z: number | null;
  affected_instruments: string[];
  fit_as_of: string | null;
}

export interface NowcastingHistoryPoint {
  value_ts: string;
  actual: number | null;
}

export interface AltDataComponent {
  instrument_id: string;
  method_id: string;
  raw_value: number | null;
  confidence: number | null;
  covered: boolean;
  extras: Record<string, unknown>;
}

// Stage 6: regime classifier
export interface RegimeMethod {
  method_id: string;
  name: string;
  status: string;
}

export interface RegimeCurrent {
  method_id: string;
  value_ts: string;
  observation_ts: string;
  label: string;
  probability_vector: Record<string, number>;
  confidence: number | null;
  transition_prob: number | null;
  days_in_regime: number | null;
}

export interface RegimeHistoryPoint {
  value_ts: string;
  label: string;
  confidence: number | null;
  transition_prob: number | null;
}

export interface RegimeAttributionRow {
  regime_method_id: string;
  regime_label: string;
  signal_method_id: string;
  value_ts: string;
  n_observations: number;
  mean_return: number | null;
  sharpe: number | null;
  hit_rate: number | null;
}

export interface ChangepointPoint {
  value_ts: string;
  changepoint_probability: number;
}

// Stage 7: composite scoring
export interface CompositeMethod {
  method_id: string;
  name: string;
  status: string;
}

export interface CompositeScoreRow {
  method_id: string;
  instrument_id: string;
  value_ts: string;
  observation_ts: string;
  raw_score: number | null;
  score: number | null;
  confidence: number | null;
  regime_label: string | null;
  n_signals_used: number | null;
}

export interface CompositeContribution {
  signal_method_id: string;
  weight: number;
  z: number;
  confidence: number;
  contribution: number;
}

export interface CompositeBreakdown {
  method_id: string;
  instrument_id: string;
  value_ts: string;
  observation_ts: string;
  score: number | null;
  raw_score: number | null;
  regime_label: string | null;
  transition_multiplier: number | null;
  transition_probability: number | null;
  contributions: CompositeContribution[];
}

export interface CompositePerRegimeWeight {
  regime_label: string;
  signal_method_id: string;
  weight: number;
  weight_source: string;
}

export interface CompositeEffectiveWeight {
  signal_method_id: string;
  effective_weight: number;
}

export interface CompositeWeights {
  method_id: string;
  snapshot_ts: string | null;
  per_regime: CompositePerRegimeWeight[];
  effective: CompositeEffectiveWeight[];
  regime_probability_vector: Record<string, number> | null;
}

export interface TransitionMultiplier {
  multiplier: number;
  changepoint_probability: number | null;
  threshold: number;
  floor: number;
}

// Stage 8: portfolio construction
export interface PortfolioMethodRow {
  method_id: string;
  component: string;
  name: string;
  status: string;
}

export interface PortfolioPositionRow {
  method_id: string;
  instrument_id: string;
  as_of: string;
  target_weight: number;
  pre_gate_weight: number | null;
  expected_vol_contribution: number | null;
  composite_score: number | null;
  block: string | null;
  gate_level: string | null;
  gate_scaling_factor: number | null;
}

export interface PortfolioPositionHistoryPoint {
  as_of: string;
  target_weight: number;
  pre_gate_weight: number | null;
}

export interface PortfolioRiskInstrument {
  instrument_id: string;
  volatility: number | null;
  vol_contribution: number | null;
  block: string | null;
}

export interface PortfolioRisk {
  method_id: string;
  as_of: string | null;
  instruments: PortfolioRiskInstrument[];
  block_exposure: Record<string, number>;
  portfolio_vol: number | null;
}

export interface DrawdownState {
  method_id: string;
  current_gate_level: string;
  effective_scaling_factor: number;
  level_1_triggered_at: string | null;
  level_1_release_at: string | null;
  level_2_triggered_at: string | null;
  level_2_release_at: string | null;
  level_3_triggered_at: string | null;
  updated_at: string | null;
}

export interface EquityPoint {
  as_of: string;
  nav: number;
  daily_return: number | null;
  cumulative_return: number | null;
  peak_nav: number | null;
  drawdown_from_peak: number | null;
}

export interface CovariancePoint {
  instrument_a: string;
  instrument_b: string;
  covariance: number | null;
  correlation: number | null;
}

export interface CovarianceMatrix {
  method_id: string;
  as_of: string | null;
  points: CovariancePoint[];
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
    api.get<ComparisonRow[]>(
      `/signals/comparisons${component ? `?component=${encodeURIComponent(component)}` : ""}`,
    ),

  // ----- positioning (Stage 4A + 4C) -----
  positioningCot: (
    instrument: string,
    params: { report_type?: string; lookback_weeks?: number } = {},
  ) => {
    const q = new URLSearchParams({ instrument });
    if (params.report_type) q.set("report_type", params.report_type);
    if (params.lookback_weeks) q.set("lookback_weeks", String(params.lookback_weeks));
    return api.get<PositioningCotPoint[]>(`/signals/positioning/cot?${q.toString()}`);
  },
  positioningBreakdown: (
    instrument_id: string,
    params: { method_id?: string; lookback_weeks?: number } = {},
  ) => {
    const q = new URLSearchParams({ instrument_id });
    if (params.method_id) q.set("method_id", params.method_id);
    if (params.lookback_weeks) q.set("lookback_weeks", String(params.lookback_weeks));
    return api.get<PositioningBreakdownPoint[]>(
      `/signals/positioning/breakdown?${q.toString()}`,
    );
  },

  // ----- dislocation (Stage 4A + 4C) -----
  dislocationFactors: (method_id = "dislocation.pca.v1") =>
    api.get<DislocationFactor[]>(
      `/signals/dislocation/factors?method_id=${encodeURIComponent(method_id)}`,
    ),
  dislocationExplainedVariance: (
    method_id = "dislocation.pca.v1",
    params: { from?: string; to?: string } = {},
  ) => {
    const q = new URLSearchParams({ method_id });
    if (params.from) q.set("from", params.from);
    if (params.to) q.set("to", params.to);
    return api.get<DislocationExplainedVariancePoint[]>(
      `/signals/dislocation/explained_variance?${q.toString()}`,
    );
  },

  // ----- factor exposure (Stage 4B + 4C) -----
  factorExposureFactors: () =>
    api.get<FactorZScore[]>("/signals/factor_exposure/factors"),
  factorExposureLoadings: (method_id = "factor_exposure.ols.v1") =>
    api.get<FactorExposureLoading[]>(
      `/signals/factor_exposure/loadings?method_id=${encodeURIComponent(method_id)}`,
    ),
  factorExposureContributions: (
    instrument_id: string,
    method_id = "factor_exposure.ols.v1",
  ) => {
    const q = new URLSearchParams({ instrument_id, method_id });
    return api.get<FactorContributions>(
      `/signals/factor_exposure/contributions?${q.toString()}`,
    );
  },

  // ----- catalyst (Stage 4B + 4C) -----
  catalystEvents: (params: { days_ahead?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.days_ahead) q.set("days_ahead", String(params.days_ahead));
    return api.get<UpcomingCatalyst[]>(`/signals/catalyst/events?${q.toString()}`);
  },
  catalystHistorical: (
    instrument_id: string,
    params: { event_subject?: string; lookback_years?: number } = {},
  ) => {
    const q = new URLSearchParams({ instrument_id });
    if (params.event_subject) q.set("event_subject", params.event_subject);
    if (params.lookback_years)
      q.set("lookback_years", String(params.lookback_years));
    return api.get<CatalystHistorical[]>(
      `/signals/catalyst/historical?${q.toString()}`,
    );
  },
  catalystPressure: (method_id = "catalyst.event_study.v1") =>
    api.get<CatalystPressure[]>(
      `/signals/catalyst/pressure?method_id=${encodeURIComponent(method_id)}`,
    ),

  // ----- vol_surface (Stage 5) -----
  volSurfaceSlices: (instrument: string, as_of?: string) => {
    const q = new URLSearchParams({ instrument });
    if (as_of) q.set("as_of", as_of);
    return api.get<VolSurfaceSlices>(`/signals/vol_surface/slices?${q.toString()}`);
  },
  volSurfaceTermStructure: (instrument: string, as_of?: string) => {
    const q = new URLSearchParams({ instrument });
    if (as_of) q.set("as_of", as_of);
    return api.get<VolSurfaceTermStructurePoint[]>(
      `/signals/vol_surface/term_structure?${q.toString()}`,
    );
  },

  // ----- nowcasting (Stage 5) -----
  nowcastingProjections: (method_id = "nowcasting.ols_ar.v1") =>
    api.get<NowcastingProjection[]>(
      `/signals/nowcasting/projections?method_id=${encodeURIComponent(method_id)}`,
    ),
  nowcastingHistory: (release: string, params: { from?: string; to?: string } = {}) => {
    const q = new URLSearchParams({ release });
    if (params.from) q.set("from", params.from);
    if (params.to) q.set("to", params.to);
    return api.get<NowcastingHistoryPoint[]>(
      `/signals/nowcasting/history?${q.toString()}`,
    );
  },

  // ----- alt_data (Stage 5) -----
  altDataComponents: () => api.get<AltDataComponent[]>("/signals/alt_data/components"),

  // ----- regime (Stage 6) -----
  regimeMethods: () => api.get<RegimeMethod[]>("/regime/methods"),
  regimeCurrent: (method_id = "regime.rules.v1") =>
    api.get<RegimeCurrent | null>(
      `/regime/current?method_id=${encodeURIComponent(method_id)}`,
    ),
  regimeHistory: (method_id = "regime.rules.v1", params: { from?: string; to?: string } = {}) => {
    const q = new URLSearchParams({ method_id });
    if (params.from) q.set("from", params.from);
    if (params.to) q.set("to", params.to);
    return api.get<RegimeHistoryPoint[]>(`/regime/history?${q.toString()}`);
  },
  regimeAttribution: (params: { regime_method?: string; signal_method?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.regime_method) q.set("regime_method", params.regime_method);
    if (params.signal_method) q.set("signal_method", params.signal_method);
    return api.get<RegimeAttributionRow[]>(`/regime/attribution?${q.toString()}`);
  },
  regimeChangepoints: (params: { from?: string; to?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.from) q.set("from", params.from);
    if (params.to) q.set("to", params.to);
    return api.get<ChangepointPoint[]>(`/regime/changepoints?${q.toString()}`);
  },

  // ----- composite (Stage 7) -----
  compositeMethods: () => api.get<CompositeMethod[]>("/composite/methods"),
  compositeScores: (params: { method_id?: string; as_of?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.method_id) q.set("method_id", params.method_id);
    if (params.as_of) q.set("as_of", params.as_of);
    return api.get<CompositeScoreRow[]>(`/composite/scores?${q.toString()}`);
  },
  compositeBreakdown: (
    instrument: string,
    params: { method_id?: string; as_of?: string } = {},
  ) => {
    const q = new URLSearchParams({ instrument });
    if (params.method_id) q.set("method_id", params.method_id);
    if (params.as_of) q.set("as_of", params.as_of);
    return api.get<CompositeBreakdown | null>(`/composite/breakdown?${q.toString()}`);
  },
  compositeWeights: (
    params: { method_id?: string; regime_method_id?: string; as_of?: string } = {},
  ) => {
    const q = new URLSearchParams();
    if (params.method_id) q.set("method_id", params.method_id);
    if (params.regime_method_id) q.set("regime_method_id", params.regime_method_id);
    if (params.as_of) q.set("as_of", params.as_of);
    return api.get<CompositeWeights>(`/composite/weights?${q.toString()}`);
  },
  compositeTransitionMultiplier: (
    params: {
      as_of?: string;
      bocpd_method_id?: string;
      threshold?: number;
      floor?: number;
    } = {},
  ) => {
    const q = new URLSearchParams();
    if (params.as_of) q.set("as_of", params.as_of);
    if (params.bocpd_method_id) q.set("bocpd_method_id", params.bocpd_method_id);
    if (params.threshold !== undefined) q.set("threshold", String(params.threshold));
    if (params.floor !== undefined) q.set("floor", String(params.floor));
    return api.get<TransitionMultiplier>(
      `/composite/transition_multiplier?${q.toString()}`,
    );
  },

  // ----- portfolio (Stage 8) -----
  portfolioMethods: () => api.get<PortfolioMethodRow[]>("/portfolio/methods"),
  portfolioPositions: (params: { method_id?: string; as_of?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.method_id) q.set("method_id", params.method_id);
    if (params.as_of) q.set("as_of", params.as_of);
    return api.get<PortfolioPositionRow[]>(`/portfolio/positions?${q.toString()}`);
  },
  portfolioPositionsHistory: (
    instrument: string,
    params: { method_id?: string; from?: string; to?: string } = {},
  ) => {
    const q = new URLSearchParams({ instrument });
    if (params.method_id) q.set("method_id", params.method_id);
    if (params.from) q.set("from", params.from);
    if (params.to) q.set("to", params.to);
    return api.get<PortfolioPositionHistoryPoint[]>(
      `/portfolio/positions/history?${q.toString()}`,
    );
  },
  portfolioRisk: (
    params: { method_id?: string; covariance_method_id?: string; as_of?: string } = {},
  ) => {
    const q = new URLSearchParams();
    if (params.method_id) q.set("method_id", params.method_id);
    if (params.covariance_method_id)
      q.set("covariance_method_id", params.covariance_method_id);
    if (params.as_of) q.set("as_of", params.as_of);
    return api.get<PortfolioRisk>(`/portfolio/risk?${q.toString()}`);
  },
  portfolioDrawdown: (method_id = "portfolio.erc.v1") =>
    api.get<DrawdownState>(
      `/portfolio/drawdown?method_id=${encodeURIComponent(method_id)}`,
    ),
  portfolioDrawdownRelease: (method_id = "portfolio.erc.v1") =>
    api.post<{ method_id: string; released: boolean; new_state: DrawdownState }>(
      `/portfolio/drawdown/release?method_id=${encodeURIComponent(method_id)}`,
    ),
  portfolioEquity: (
    params: { method_id?: string; from?: string; to?: string } = {},
  ) => {
    const q = new URLSearchParams();
    if (params.method_id) q.set("method_id", params.method_id);
    if (params.from) q.set("from", params.from);
    if (params.to) q.set("to", params.to);
    return api.get<EquityPoint[]>(`/portfolio/equity?${q.toString()}`);
  },
  portfolioCovariance: (params: { method_id?: string; as_of?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.method_id) q.set("method_id", params.method_id);
    if (params.as_of) q.set("as_of", params.as_of);
    return api.get<CovarianceMatrix>(`/portfolio/covariance?${q.toString()}`);
  },
};
