import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import SignalsCatalyst from "../pages/SignalsCatalyst";

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
        <SignalsCatalyst />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SignalsCatalyst page", () => {
  it("renders the header and the days-ahead control", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /catalyst/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/days ahead/i)).toBeInTheDocument();
  });

  it("renders the method selector with event-study + causal", async () => {
    renderPage();
    const select = await screen.findByLabelText(/method/i);
    expect(select).toBeInTheDocument();
    expect(screen.getByText(/Event-study \(baseline\)/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Causal \(placeholder until Phase 2 lands\)/i),
    ).toBeInTheDocument();
  });

  it("shows the empty-events state when API returns []", async () => {
    renderPage();
    expect(
      await screen.findByText(/no events in this window/i),
    ).toBeInTheDocument();
  });
});
