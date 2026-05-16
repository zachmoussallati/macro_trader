import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import SignalsPositioning from "../pages/SignalsPositioning";

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
        <SignalsPositioning />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SignalsPositioning page", () => {
  it("renders the header and instrument selector", async () => {
    renderPage();
    expect(
      await screen.findByRole("heading", { name: /positioning/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/instrument/i)).toBeInTheDocument();
  });

  it("shows the choose-an-instrument prompt before selection", async () => {
    renderPage();
    expect(
      await screen.findByText(/pick an instrument/i),
    ).toBeInTheDocument();
  });
});
