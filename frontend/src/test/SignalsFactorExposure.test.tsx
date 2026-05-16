import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import SignalsFactorExposure from "../pages/SignalsFactorExposure";

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
        <SignalsFactorExposure />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SignalsFactorExposure page", () => {
  it("renders the header", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /factor exposure/i }),
    ).toBeInTheDocument();
  });

  it("lists all three methods including the EconML-gated Causal Forest", async () => {
    renderPage();
    expect(await screen.findByText(/OLS \(baseline\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Random Forest/i)).toBeInTheDocument();
    expect(screen.getByText(/Causal Forest \(needs \[ml\] extra\)/i)).toBeInTheDocument();
  });
});
