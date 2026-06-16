import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NodeMetrics from "./NodeMetrics";
import * as workerService from "@/services/workerService";
import type { NodeMetricsSample } from "@/services/workerService";

// recharts ResponsiveContainer needs measured size jsdom lacks; stub it.
vi.mock("recharts", async (orig) => {
  const actual = await orig<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div style={{ width: 800, height: 260 }}>{children}</div>
    ),
  };
});

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const sample = (over: Partial<NodeMetricsSample> = {}): NodeMetricsSample => ({
  ts: "2026-06-16T00:00:01Z", cpu_pct: 30, mem_used_bytes: 1024 ** 3,
  mem_total_bytes: 4 * 1024 ** 3, net_rx_bps: 1024, net_tx_bps: 2048,
  disk_read_bps: 512, disk_write_bps: 256,
  gpus: [{ index: 0, name: "A100", util_pct: 50, mem_used_mib: 1024, mem_total_mib: 81920 }],
  ...over,
});

describe("NodeMetrics", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows a placeholder when the node is not ready", () => {
    wrap(<NodeMetrics nodeId="n1" nodeState="provisioning" currentPhase="bootstrapping" />);
    expect(screen.getByText(/available once the worker registers/i)).toBeInTheDocument();
  });

  it("renders charts when ready with samples", async () => {
    vi.spyOn(workerService, "getNodeMetrics").mockResolvedValue({
      latest: sample(), samples: [sample(), sample({ ts: "2026-06-16T00:00:06Z" })],
    });
    wrap(<NodeMetrics nodeId="n1" nodeState="ready" />);
    await waitFor(() => expect(screen.getByText(/CPU Utilization/i)).toBeInTheDocument());
    expect(screen.getByText(/Memory/i)).toBeInTheDocument();
    expect(screen.getByText(/GPU 0/i)).toBeInTheDocument();
    expect(screen.getByText(/Network/i)).toBeInTheDocument();
    expect(screen.getByText(/Disk/i)).toBeInTheDocument();
  });

  it("shows an empty state when ready but no samples yet", async () => {
    vi.spyOn(workerService, "getNodeMetrics").mockResolvedValue({ latest: null, samples: [] });
    wrap(<NodeMetrics nodeId="n1" nodeState="ready" />);
    await waitFor(() =>
      expect(screen.getByText(/waiting for the first metrics sample/i)).toBeInTheDocument(),
    );
  });

  it("shows an error state when the request fails", async () => {
    vi.spyOn(workerService, "getNodeMetrics").mockRejectedValue(new Error("boom"));
    wrap(<NodeMetrics nodeId="n1" nodeState="ready" />);
    await waitFor(() =>
      expect(screen.getByText(/couldn.t load metrics/i)).toBeInTheDocument(),
    );
  });

  it("shows a loading state while the request is in flight", () => {
    vi.spyOn(workerService, "getNodeMetrics").mockReturnValue(
      new Promise(() => {}) as Promise<workerService.NodeMetricsResponse>,
    );
    wrap(<NodeMetrics nodeId="n1" nodeState="ready" />);
    expect(screen.getByText(/loading metrics/i)).toBeInTheDocument();
  });

  it("omits GPU panels when the node has no GPUs", async () => {
    const noGpu = (): workerService.NodeMetricsSample => ({
      ts: "2026-06-16T00:00:01Z", cpu_pct: 10, mem_used_bytes: 1024 ** 3,
      mem_total_bytes: 2 * 1024 ** 3, net_rx_bps: 1, net_tx_bps: 2,
      disk_read_bps: 3, disk_write_bps: 4, gpus: [],
    });
    vi.spyOn(workerService, "getNodeMetrics").mockResolvedValue({
      latest: noGpu(), samples: [noGpu()],
    });
    wrap(<NodeMetrics nodeId="n1" nodeState="ready" />);
    await waitFor(() => expect(screen.getByText(/CPU Utilization/i)).toBeInTheDocument());
    expect(screen.queryByText(/GPU Utilization/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/GPU VRAM/i)).not.toBeInTheDocument();
  });
});
