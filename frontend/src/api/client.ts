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

export const apiMethods = {
  health: () => api.get<HealthResponse>("/health"),
  listMethods: () => api.get<MethodRow[]>("/methods"),
  login: (email: string, password: string) =>
    api.post<TokenPair>("/auth/login", { email, password }),
  me: () => api.get<UserOut>("/auth/me"),
};
