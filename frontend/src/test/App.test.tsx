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
    } else if (url.includes("/methods")) {
      body = [];
    } else {
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
