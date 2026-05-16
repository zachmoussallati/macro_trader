import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import SignalsNowcasting from "../pages/SignalsNowcasting";

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
        <SignalsNowcasting />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SignalsNowcasting page", () => {
  it("renders the header", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /nowcasting/i }),
    ).toBeInTheDocument();
  });

  it("lists OLS-AR + BVAR methods", async () => {
    renderPage();
    expect(await screen.findByText(/OLS-AR \(baseline\)/i)).toBeInTheDocument();
    expect(screen.getByText(/BVAR \(shadow\)/i)).toBeInTheDocument();
  });

  it("shows the no-projections empty state when API returns []", async () => {
    renderPage();
    expect(
      await screen.findByText(/no projections yet/i),
    ).toBeInTheDocument();
  });
});
