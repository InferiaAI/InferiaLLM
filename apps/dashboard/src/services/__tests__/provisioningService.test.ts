import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  getProvisioning,
  getProvisioningLogs,
  getEC2Console,
  type ProvisioningSummary,
} from "../provisioningService";

const getMock = vi.fn();
vi.mock("@/lib/api", () => ({
  computeApi: {
    get: (path: string) => getMock(path).then((r: any) => ({ data: r })),
  },
}));

describe("provisioningService", () => {
  beforeEach(() => getMock.mockReset());

  it("getProvisioning returns the summary shape", async () => {
    const payload: ProvisioningSummary = {
      current_phase: "pulumi_up",
      terminal: false,
      phases: [{ phase: "prepare", status: "succeeded",
                 started_at: "2026-05-25T00:00:00Z",
                 ended_at: "2026-05-25T00:00:01Z",
                 last_message: null }],
    };
    getMock.mockResolvedValueOnce(payload);
    const s = await getProvisioning("node-1");
    expect(s).toEqual(payload);
    expect(getMock).toHaveBeenCalledWith("/nodes/node-1/provisioning");
  });

  it("getProvisioningLogs passes the cursor", async () => {
    getMock.mockResolvedValueOnce({ events: [], next_after: null });
    await getProvisioningLogs("node-1", 42);
    expect(getMock).toHaveBeenCalledWith(
      "/nodes/node-1/provisioning-logs?after=42",
    );
  });

  it("getProvisioningLogs defaults cursor to 0", async () => {
    getMock.mockResolvedValueOnce({ events: [], next_after: null });
    await getProvisioningLogs("node-1");
    expect(getMock).toHaveBeenCalledWith(
      "/nodes/node-1/provisioning-logs?after=0",
    );
  });

  it("getEC2Console returns lines + fetched_at", async () => {
    getMock.mockResolvedValueOnce({
      logs: ["[boot] line1"], fetched_at: "2026-05-25T00:00:00Z",
    });
    const c = await getEC2Console("node-1");
    expect(c.logs).toEqual(["[boot] line1"]);
  });
});
