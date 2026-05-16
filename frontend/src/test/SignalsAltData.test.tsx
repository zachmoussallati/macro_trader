import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import SignalsAltData from "../pages/SignalsAltData";

beforeEach(() => {
  global.fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: () => Promise.resolve([]),
  }) as unknown as typeof fetch;
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SignalsAltData />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SignalsAltData page", () => {
  it("renders the header", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /alt data/i }),
    ).toBeInTheDocument();
  });

  it("renders the four filter buttons", async () => {
    renderPage();
    expect(await screen.findByText(/All/i)).toBeInTheDocument();
    expect(screen.getByText(/EIA only/i)).toBeInTheDocument();
    expect(screen.getByText(/USDA only/i)).toBeInTheDocument();
    expect(screen.getByText(/Trends only/i)).toBeInTheDocument();
  });

  it("shows the no-rows empty state when API returns []", async () => {
    renderPage();
    expect(
      await screen.findByText(/no alt-data rows yet/i),
    ).toBeInTheDocument();
  });
});
