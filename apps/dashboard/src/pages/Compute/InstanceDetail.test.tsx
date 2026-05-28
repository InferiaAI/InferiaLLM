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
// ProvisioningStatus is mocked to surface the attemptCount prop so tests
// can assert the "Attempt N" badge wiring without re-implementing the
// component's phase rendering. The real badge logic is covered in
// ProvisioningStatus's own test.
vi.mock("@/components/nodes/ProvisioningStatus", () => ({
  default: ({ attemptCount }: { attemptCount?: number }) => (
    <div>
      Provisioning Status
      {attemptCount !== undefined && attemptCount > 1 && (
        <span data-testid="attempt-badge">Attempt {attemptCount}</span>
      )}
    </div>
  ),
}));
// AWSMetadataGrid is mocked to render the two field labels the test
// suite asserts on. The real grid's six-field rendering is covered by
// AWSMetadataGrid.test.tsx — we only need to verify the InstanceDetail
// wiring (provider=aws + aws_metadata present → grid shows).
vi.mock("@/components/nodes/AWSMetadataGrid", () => ({
  AWSMetadataGrid: () => (
    <div>
      <div>Instance class</div>
      <div>Public DNS</div>
    </div>
  ),
}));
// RetryProvisioningButton is mocked because the real one uses
// useMutation, which would require a QueryClientProvider wrapper here.
// Its own test covers the POST/disable/onSuccess behaviour; we only
// care that the error banner mounts it when phase=failed + error is set.
vi.mock("@/components/nodes/RetryProvisioningButton", () => ({
  RetryProvisioningButton: ({ nodeId }: { nodeId: string }) => (
    <button type="button" data-node-id={nodeId}>Retry</button>
  ),
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


// ---------------------------------------------------------------------------
// T30: Overview tab wiring for the new ProvisioningSummary fields
// (error, aws_metadata, attempt_count, job_id) added in T24.
// ---------------------------------------------------------------------------
describe("InstanceDetail Overview T24 fields wiring", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

  // The mocked AWSMetadataGrid renders the two field labels the plan
  // calls out. We assert the grid mounts when provider=aws + aws_metadata
  // is non-null in the /provisioning response.
  it("shows AWSMetadataGrid when provider=aws", async () => {
    (getNode as any).mockResolvedValue(baseNode({
      state: "ready", provider: "aws",
    }));
    (getProvisioning as any).mockResolvedValue({
      current_phase: "ready", terminal: true,
      phases: [
        { phase: "ready", status: "succeeded", started_at: "x", ended_at: "y", last_message: null },
      ],
      attempt_count: 1,
      error: null,
      aws_metadata: {
        instance_class: "normal_gpu",
        instance_type:  "g6.xlarge",
        region:         "us-east-1",
        ami_id:         "ami-deadbeef",
        instance_id:    "i-0abc1234",
        public_dns:     "ec2-1-2-3-4.compute-1.amazonaws.com",
      },
      job_id: "j-1",
    });
    renderAt("n1");
    // Flush initial getNode() and the immediate getProvisioning() tick.
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText("Instance class")).toBeInTheDocument();
    expect(screen.getByText("Public DNS")).toBeInTheDocument();
  });

  // The red error banner mounts only when provisioning.error is set. We
  // verify the mocked Retry button appears AND the error.hint text is
  // shown (the plan's reference assertion).
  it("shows Retry button when phase=failed and error fields are set", async () => {
    (getNode as any).mockResolvedValue(baseNode({
      state: "provisioning", provider: "aws",
    }));
    (getProvisioning as any).mockResolvedValue({
      current_phase: "failed", terminal: true,
      phases: [
        { phase: "prepare", status: "succeeded", started_at: "x", ended_at: "y", last_message: null },
        { phase: "pulumi_up", status: "failed",  started_at: "x", ended_at: "y",
          last_message: "pulumi binary not found" },
      ],
      attempt_count: 1,
      error: {
        code: "PULUMI_CLI_MISSING",
        message: "Pulumi CLI not installed on the orchestrator host",
        hint:    "Install Pulumi and restart the orchestration service.",
        class:   "PERMANENT",
      },
      aws_metadata: null,
      job_id: "j-1",
    });
    renderAt("n1");
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
    expect(screen.getByText(/Pulumi CLI not installed/i)).toBeInTheDocument();
  });

  // The "Attempt N" badge mounts only when attempt_count > 1. The mocked
  // ProvisioningStatus surfaces the prop so we can assert it without
  // re-implementing the component's render.
  it("shows attempt-count badge when attempt_count > 1", async () => {
    (getNode as any).mockResolvedValue(baseNode({
      state: "provisioning", provider: "aws",
    }));
    (getProvisioning as any).mockResolvedValue({
      current_phase: "pulumi_up", terminal: false,
      phases: [
        { phase: "pulumi_up", status: "running", started_at: "x", ended_at: null, last_message: "creating ec2" },
      ],
      attempt_count: 3,
      error: null,
      aws_metadata: null,
      job_id: "j-1",
    });
    renderAt("n1");
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText(/attempt 3/i)).toBeInTheDocument();
  });

  // Negative case: provider=worker (non-aws) must NEVER mount the grid,
  // even if the (hypothetical) response includes aws_metadata. The
  // provider gate is the safety belt here — the polling effect itself
  // short-circuits for non-aws providers, so the response is empty in
  // practice, but the JSX gate is the load-bearing check.
  it("does NOT show AWSMetadataGrid when provider=worker", async () => {
    (getNode as any).mockResolvedValue(baseNode({
      state: "ready", provider: "on_prem", agent_kind: "worker",
    }));
    (getProvisioning as any).mockResolvedValue({
      current_phase: null, terminal: true, phases: [],
      attempt_count: 0, error: null, aws_metadata: null, job_id: null,
    });
    renderAt("n1");
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.queryByText("Instance class")).not.toBeInTheDocument();
  });
});
