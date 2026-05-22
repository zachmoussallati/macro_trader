import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Portfolio from "../pages/Portfolio";

beforeEach(() => {
  global.fetch = vi.fn().mockImplementation((url: string) => {
    if (typeof url === "string" && url.includes("/portfolio/drawdown")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            method_id: "portfolio.erc.v1",
            current_gate_level: "none",
            effective_scaling_factor: 1.0,
            level_1_triggered_at: null,
            level_1_release_at: null,
            level_2_triggered_at: null,
            level_2_release_at: null,
            level_3_triggered_at: null,
            updated_at: null,
          }),
      });
    }
    if (typeof url === "string" && url.includes("/portfolio/risk")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            method_id: "portfolio.erc.v1",
            as_of: null,
            instruments: [],
            block_exposure: {},
            portfolio_vol: null,
          }),
      });
    }
    // /portfolio/methods, /portfolio/positions, /portfolio/equity all
    // return arrays.
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve([]),
    });
  }) as unknown as typeof fetch;
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Portfolio />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Portfolio page", () => {
  it("renders the header", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /portfolio/i }),
    ).toBeInTheDocument();
  });

  it("shows the no-methods empty state when /portfolio/methods is empty", async () => {
    renderPage();
    expect(
      await screen.findByText(/no portfolio methods registered yet/i),
    ).toBeInTheDocument();
  });

  it("shows the no-positions empty state when /portfolio/positions is empty", async () => {
    renderPage();
    expect(
      await screen.findByText(/no positions yet/i),
    ).toBeInTheDocument();
  });

  it("shows the no-equity-curve message when equity history is empty", async () => {
    renderPage();
    expect(
      await screen.findByText(/no equity history yet/i),
    ).toBeInTheDocument();
  });

  it("renders the drawdown card", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /drawdown gate/i }),
    ).toBeInTheDocument();
  });
});
