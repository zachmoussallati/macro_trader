import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import App from "../App";

beforeEach(() => {
  // Home fires four queries: /health, /data/freshness, /data/quality/summary,
  // /calendar/events. Return URL-appropriate stub responses so the page
  // renders without runtime errors.
  global.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    let body: unknown = null;
    if (url.includes("/health")) {
      body = {
        service: "macro-trader",
        version: "0.1.0",
        now: new Date().toISOString(),
        healthy: true,
        checks: { database: { ok: true } },
      };
    } else if (url.includes("/data/freshness")) {
      body = [];
    } else if (url.includes("/data/quality/summary")) {
      body = {};
    } else if (url.includes("/calendar/events")) {
      body = [];
    } else if (url.includes("/regime/current")) {
      // /regime/current returns null when no state exists (single object,
      // not a list) — the Home regime card defensive-checks against null.
      body = null;
    } else if (url.includes("/composite/transition_multiplier")) {
      body = {
        multiplier: 1.0,
        changepoint_probability: null,
        threshold: 0.5,
        floor: 0.5,
      };
    } else if (url.includes("/portfolio/drawdown")) {
      body = {
        method_id: "portfolio.erc.v1",
        current_gate_level: "none",
        effective_scaling_factor: 1.0,
        level_1_triggered_at: null,
        level_1_release_at: null,
        level_2_triggered_at: null,
        level_2_release_at: null,
        level_3_triggered_at: null,
        updated_at: null,
      };
    } else if (url.includes("/methods")) {
      body = [];
    } else {
      // Default to empty list for everything else (most endpoints are
      // list-shape — signals, calendar, positions, composite scores).
      body = [];
    }
    return {
      ok: true,
      status: 200,
      json: async () => body,
    } as unknown as Response;
  }) as unknown as typeof fetch;
});

describe("App", () => {
  it("renders the Home page title", async () => {
    render(<App />);
    expect(await screen.findByText("Macro Trader")).toBeInTheDocument();
  });

  it("links to the methods page", async () => {
    render(<App />);
    expect(await screen.findByRole("link", { name: /methods/i })).toBeInTheDocument();
  });
});
