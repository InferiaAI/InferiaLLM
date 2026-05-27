import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import InstanceDetail from "./InstanceDetail";

vi.mock("@/services/nodeService", () => ({
  getNode:      vi.fn(),
  patchLabels:  vi.fn(),
  deleteNode:   vi.fn(),
}));
vi.mock("@/services/provisioningService", () => ({
  getProvisioning: vi.fn(),
  ALL_PHASES: ["prepare","ami_lookup","pulumi_init","pulumi_up","ec2_running","cloud_init","worker_bootstrap","ready"],
}));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ hasPermission: () => true }),
}));
vi.mock("@/components/nodes/NodeLogs", () => ({ default: () => <div>logs</div> }));
vi.mock("@/components/nodes/NodeShell", () => ({ default: () => <div>shell</div> }));
vi.mock("@/components/nodes/LabelEditor", () => ({ default: () => <div>labels</div> }));
vi.mock("@/components/nodes/ProvisioningStatus", () => ({
  default: () => <div>Provisioning Status</div>,
}));

const { getNode } = await import("@/services/nodeService");
const { getProvisioning } = await import("@/services/provisioningService");

function renderAt(id: string) {
  return render(
    <MemoryRouter initialEntries={[`/dashboard/compute/nodes/${id}`]}>
      <Routes>
        <Route path="/dashboard/compute/nodes/:id" element={<InstanceDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

const baseNode = (overrides: any = {}) => ({
  id: "n1", pool_id: "p1", node_name: "test",
  agent_kind: null, provider: "aws", state: "provisioning",
  labels: {}, advertise_url: null, expose_url: null,
  gpu_total: 0, gpu_allocated: 0, vcpu_total: 0, vcpu_allocated: 0,
  ram_gb_total: 0, ram_gb_allocated: 0, last_heartbeat: null,
  provider_instance_id: "placeholder:p1",
  ...overrides,
});

describe("InstanceDetail (AWS provisioning)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (getNode as any).mockResolvedValue(baseNode());
    (getProvisioning as any).mockResolvedValue({
      current_phase: "pulumi_up", terminal: false,
      phases: [
        { phase: "prepare", status: "succeeded", started_at: "x", ended_at: "y", last_message: null },
        { phase: "pulumi_up", status: "running",  started_at: "x", ended_at: null, last_message: "creating ec2" },
      ],
    });
  });
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  it("shows Logs and Shell tabs for aws provider even when state != ready", async () => {
    renderAt("n1");
    // Flush the initial getNode() promise so the component exits the loading state
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole("button", { name: /Logs/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Shell/i })).toBeInTheDocument();
  });

  it("renders ProvisioningStatus card on Overview when state=provisioning", async () => {
    renderAt("n1");
    // Flush getNode, then the provisioning effect fires on the next tick
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    // Flush the getProvisioning() call
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(getProvisioning).toHaveBeenCalled();
    expect(screen.getByText(/Provisioning Status/i)).toBeInTheDocument();
  });

  it("polls /provisioning every 2s when state=provisioning", async () => {
    renderAt("n1");
    // Flush initial load
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    // Flush first provisioning tick
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(getProvisioning).toHaveBeenCalledTimes(1);
    // Advance 2s to trigger the interval
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(getProvisioning).toHaveBeenCalledTimes(2);
  });

  it("polls every 15s when state=ready (no fast provisioning poll)", async () => {
    (getNode as any).mockResolvedValue(baseNode({ state: "ready", agent_kind: "worker" }));
    (getProvisioning as any).mockResolvedValue({
      current_phase: null, terminal: true, phases: [],
    });
    renderAt("n1");
    // Flush initial load
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(getNode).toHaveBeenCalledTimes(1);
    // Advance 2s — should NOT trigger another getNode poll (interval is 15s)
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(getNode).toHaveBeenCalledTimes(1);
    // Advance remaining 13s to reach the 15s mark
    await act(async () => { await vi.advanceTimersByTimeAsync(13000); });
    expect(getNode).toHaveBeenCalledTimes(2);
  });
});


describe("InstanceDetail delete confirmation copy", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (getProvisioning as any).mockResolvedValue({
      current_phase: null, terminal: true, phases: [],
    });
  });
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  it("AWS provider gets the EC2-terminates copy", async () => {
    (getNode as any).mockResolvedValue(baseNode({
      state: "ready", provider: "aws", agent_kind: null,
    }));
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderAt("n1");
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const deleteBtn = screen.getByRole("button", { name: /Delete/i });
    deleteBtn.click();
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    const msg = confirmSpy.mock.calls[0][0] as string;
    expect(msg).toMatch(/terminates the EC2 instance/i);
    expect(msg).toMatch(/up to 90 seconds/i);
    expect(msg).not.toMatch(/soft delete/i);
    confirmSpy.mockRestore();
  });

  it("non-AWS provider gets the legacy soft-delete-ish copy", async () => {
    (getNode as any).mockResolvedValue(baseNode({
      state: "ready", provider: "on_prem", agent_kind: "worker",
    }));
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderAt("n1");
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    const deleteBtn = screen.getByRole("button", { name: /Delete/i });
    deleteBtn.click();
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    const msg = confirmSpy.mock.calls[0][0] as string;
    expect(msg).toMatch(/marked terminated/i);
    expect(msg).not.toMatch(/EC2/i);
    confirmSpy.mockRestore();
  });
});
