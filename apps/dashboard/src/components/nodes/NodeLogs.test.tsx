import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import NodeLogs from "./NodeLogs";

vi.mock("@/services/provisioningService", () => ({
  getProvisioningLogs: vi.fn(),
  getEC2Console:       vi.fn(),
}));

const { getProvisioningLogs, getEC2Console } =
  await import("@/services/provisioningService");

describe("NodeLogs (AWS provisioning mode)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    (getProvisioningLogs as any).mockResolvedValue({
      events: [
        { id: 1, phase: "pulumi_up", status: "log",
          message: "create ec2", created_at: "2026-05-25T00:00:00Z" },
      ],
      next_after: 1,
    });
  });
  afterEach(() => vi.useRealTimers());

  it("polls /provisioning-logs every 2s when provider=aws and state=provisioning", async () => {
    render(<NodeLogs nodeId="n1" nodeProvider="aws" nodeState="provisioning" />);
    // Flush the initial tick by advancing 0ms (drains pending promise microtasks too)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getProvisioningLogs).toHaveBeenCalledWith("n1", 0);
    expect(screen.getByText(/create ec2/)).toBeInTheDocument();

    // Advance 2s to trigger the interval tick
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(getProvisioningLogs).toHaveBeenLastCalledWith("n1", 1);
  });

  it("does not poll provisioning when state='ready' (delegates to WS path)", async () => {
    render(<NodeLogs nodeId="n1" nodeProvider="aws" nodeState="ready" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(getProvisioningLogs).not.toHaveBeenCalled();
  });

  it("fetches EC2 console when the user clicks the button", async () => {
    (getEC2Console as any).mockResolvedValue({
      logs: ["[boot] cloud-init"], fetched_at: "2026-05-25T00:00:00Z",
    });
    render(<NodeLogs nodeId="n1" nodeProvider="aws" nodeState="provisioning" />);
    // Wait for initial render to settle
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const btn = screen.getByRole("button", { name: /fetch ec2 console/i });
    await act(async () => {
      btn.click();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getEC2Console).toHaveBeenCalledWith("n1");
    expect(screen.getByText(/cloud-init/)).toBeInTheDocument();
  });
});
