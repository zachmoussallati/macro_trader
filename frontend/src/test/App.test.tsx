import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import App from "../App";

beforeEach(() => {
  // The health query is fired on Home; mock fetch globally to avoid network.
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        service: "macro-trader",
        version: "0.1.0",
        now: new Date().toISOString(),
        healthy: true,
        checks: { database: { ok: true } },
      }),
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
