import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import SignalsVolSurface from "../pages/SignalsVolSurface";

beforeEach(() => {
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        instrument_id: "GLD",
        snapshot_ts: "2024-12-30T00:00:00Z",
        underlying_price: null,
        slices: [],
      }),
  }) as unknown as typeof fetch;
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SignalsVolSurface />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SignalsVolSurface page", () => {
  it("renders the header", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /vol surface/i }),
    ).toBeInTheDocument();
  });

  it("shows the data-source + backtest-not-supported badges", async () => {
    renderPage();
    expect(
      await screen.findByText(/data_source: yfinance/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/historical_backtest_supported: false/i),
    ).toBeInTheDocument();
  });

  it("shows the no-chain-data empty state when API returns no slices", async () => {
    renderPage();
    expect(
      await screen.findByText(/no chain data yet/i),
    ).toBeInTheDocument();
  });
});
