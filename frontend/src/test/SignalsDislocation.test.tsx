import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import SignalsDislocation from "../pages/SignalsDislocation";

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
        <SignalsDislocation />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SignalsDislocation page", () => {
  it("renders the header", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /dislocation/i }),
    ).toBeInTheDocument();
  });

  it("renders the method selector with PCA + DFM options", async () => {
    renderPage();
    const select = await screen.findByLabelText(/method/i);
    expect(select).toBeInTheDocument();
    expect(screen.getByText(/PCA \(baseline\)/i)).toBeInTheDocument();
    expect(screen.getByText(/DFM \(shadow\)/i)).toBeInTheDocument();
  });

  it("shows the no-data empty state when API returns []", async () => {
    renderPage();
    expect(
      await screen.findByText(/no dislocation data yet/i),
    ).toBeInTheDocument();
  });
});
