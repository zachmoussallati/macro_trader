import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Composite from "../pages/Composite";

beforeEach(() => {
  global.fetch = vi.fn().mockImplementation((url: string) => {
    // /composite/weights returns an object, not an array; everything
    // else is fine returning [] (matches Pydantic List[*] response).
    if (typeof url === "string" && url.includes("/composite/weights")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            method_id: "composite.linear.v1",
            snapshot_ts: null,
            per_regime: [],
            effective: [],
            regime_probability_vector: null,
          }),
      });
    }
    if (
      typeof url === "string" &&
      url.includes("/composite/transition_multiplier")
    ) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            multiplier: 1.0,
            changepoint_probability: null,
            threshold: 0.5,
            floor: 0.5,
          }),
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
        <Composite />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Composite page", () => {
  it("renders the header", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /composite/i }),
    ).toBeInTheDocument();
  });

  it("shows the no-methods empty state when /composite/methods is empty", async () => {
    renderPage();
    expect(
      await screen.findByText(/no composite methods registered yet/i),
    ).toBeInTheDocument();
  });

  it("shows the no-scores empty state when /composite/scores is empty", async () => {
    renderPage();
    expect(
      await screen.findByText(/no composite scores yet/i),
    ).toBeInTheDocument();
  });

  it("shows the no-weights-snapshot empty state when the snapshot has no rows", async () => {
    renderPage();
    expect(
      await screen.findByText(/no weight snapshot yet/i),
    ).toBeInTheDocument();
  });

  it("prompts to pick an instrument before the breakdown loads", async () => {
    renderPage();
    expect(
      await screen.findByText(/pick a row from the ranked table/i),
    ).toBeInTheDocument();
  });
});
