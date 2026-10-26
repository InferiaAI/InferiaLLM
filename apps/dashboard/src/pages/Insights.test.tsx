import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/insightsService", () => ({
  insightsService: {
    getSummary: vi.fn(),
    getTimeseries: vi.fn(),
    getLogs: vi.fn(),
    getFilters: vi.fn(),
    getTopIps: vi.fn(),
    getTopModels: vi.fn(),
  },
}));

// The charts are lazy and need a real layout; they are not under test here.
vi.mock("@/components/insights/InsightsCharts", () => ({
  RequestVolumeChart: () => null,
  TokenUsageChart: () => null,
  ModelDistributionChart: () => null,
  TrafficByIpChart: () => null,
}));

import { insightsService } from "@/services/insightsService";
import Insights from "./Insights";

const service = insightsService as unknown as Record<string, ReturnType<typeof vi.fn>>;
const REQUEST_ID = "3f9a1c0e-7b2d-4a6f-8e1c-3b5a7d9f0e2c";

function log(requestId: string | null) {
  return {
    id: `log-${requestId}`, deployment_id: "dep-12345678", user_id: "apikey:k1", model: "qwen",
    ip_address: "10.0.0.1", request_id: requestId, request_payload: null, latency_ms: 120,
    ttft_ms: null, tokens_per_second: null, prompt_tokens: 10, completion_tokens: 5,
    total_tokens: 15, status_code: 200, error_message: null, is_streaming: false,
    applied_policies: null, created_at: "2026-10-26T10:00:00Z",
  };
}

function renderPage(url = "/dashboard/insights") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Insights />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as never;
  const totals = { requests: 2, successful_requests: 2, failed_requests: 0, success_rate: 100,
    prompt_tokens: 20, completion_tokens: 10, total_tokens: 30 };
  service.getSummary.mockResolvedValue({ totals, latency_ms: { avg: 120, samples: 2 },
    ttft_ms: { avg: 0, samples: 0 }, throughput: { requests_per_minute: 1, tokens_per_second: 1, avg_tokens_per_second: 1 } });
  service.getTimeseries.mockResolvedValue({ granularity: "day", buckets: [] });
  service.getFilters.mockResolvedValue({ deployments: [], models: [], ip_addresses: [], status_options: [], deployment_types: [] });
  service.getTopIps.mockResolvedValue({ items: [] });
  service.getTopModels.mockResolvedValue({ items: [] });
  service.getLogs.mockResolvedValue({ items: [log(REQUEST_ID), log(null)], pagination: { limit: 25, offset: 0, total: 2 } });
});

afterEach(() => vi.clearAllMocks());

describe("Insights request ID", () => {
  it("shows each log's request ID, shortened, with the full ID on hover", async () => {
    renderPage();
    const cell = await screen.findByTitle(REQUEST_ID);
    expect(cell).toHaveTextContent("3f9a1c0e-7b2…");
    expect(screen.getByRole("columnheader", { name: "Request ID" })).toBeInTheDocument();
  });

  it("searches the logs by request ID, and only the logs", async () => {
    renderPage();
    await screen.findByTitle(REQUEST_ID);
    await userEvent.type(screen.getByLabelText("Find a request by its ID"), "trace-42");
    await waitFor(() =>
      expect(service.getLogs).toHaveBeenLastCalledWith(expect.objectContaining({ request_id: "trace-42", offset: 0 })));
    for (const call of service.getSummary.mock.calls) expect(call[0]).not.toHaveProperty("request_id");
  });

  it("opens with the search from the link, and says when nothing matches", async () => {
    service.getLogs.mockResolvedValue({ items: [], pagination: { limit: 25, offset: 0, total: 0 } });
    renderPage("/dashboard/insights?requestId=trace-404");
    expect(await screen.findByText("No log with this request ID in the selected time range.")).toBeInTheDocument();
    expect(screen.getByLabelText("Find a request by its ID")).toHaveValue("trace-404");
    expect(service.getLogs).toHaveBeenCalledWith(expect.objectContaining({ request_id: "trace-404" }));
  });
});
