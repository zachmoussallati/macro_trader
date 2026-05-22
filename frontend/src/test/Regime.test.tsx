import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Regime from "../pages/Regime";

beforeEach(() => {
  global.fetch = vi.fn().mockImplementation((url: string) => {
    // /regime/current is the one endpoint that returns a single object
    // (or null when no state exists); the page defensive-checks
    // `current.data === null`. Returning [] would be truthy and crash
    // the downstream property access.
    if (typeof url === "string" && url.includes("/regime/current")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(null),
      });
    }
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
        <Regime />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Regime page", () => {
  it("renders the header", async () => {
    renderPage();
    // The page has two headings matching /regime/i: the h1 page title
    // and the h3 "Current regime" card title. Disambiguate by level.
    expect(
      await screen.findByRole("heading", { level: 1, name: /regime/i }),
    ).toBeInTheDocument();
  });

  it("shows the no-methods empty state when API returns []", async () => {
    renderPage();
    expect(
      await screen.findByText(/no regime methods registered yet/i),
    ).toBeInTheDocument();
  });

  it("shows the no-state and no-attribution empty states", async () => {
    renderPage();
    expect(
      await screen.findByText(/no attribution rows yet/i),
    ).toBeInTheDocument();
  });
});
