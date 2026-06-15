import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/api", () => ({
  computeApi: { get: vi.fn() },
}));

import { computeApi } from "@/lib/api";
import { getNodeMetrics } from "@/services/workerService";

describe("getNodeMetrics", () => {
  beforeEach(() => vi.clearAllMocks());

  it("requests the node metrics endpoint and returns the payload", async () => {
    (computeApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { latest: { ts: "b", cpu_pct: 2 }, samples: [{ ts: "a" }, { ts: "b" }] },
    });
    const res = await getNodeMetrics("node-1");
    expect(computeApi.get).toHaveBeenCalledWith("/admin/workers/node-1/metrics");
    expect(res.samples).toHaveLength(2);
    expect(res.latest?.cpu_pct).toBe(2);
  });

  it("defaults to empty when the server returns nothing", async () => {
    (computeApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: undefined });
    const res = await getNodeMetrics("node-1");
    expect(res).toEqual({ latest: null, samples: [] });
  });
});
