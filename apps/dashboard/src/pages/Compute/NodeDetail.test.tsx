import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import NodeDetail from "./NodeDetail";

// ---------------------------------------------------------------------------
// Module mocks — these must be hoisted above imports via vi.mock factory fns
// ---------------------------------------------------------------------------

vi.mock("@/services/nodeService", () => ({
  getNode: vi.fn(),
  deleteNode: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Spy on react-router-dom's useNavigate so we can assert post-delete redirects
const navigateSpy = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => navigateSpy,
  };
});

vi.mock("@/services/provisioningService", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/provisioningService")>();
  return {
    ...actual,
    getProvisioning: vi.fn(),
    retryProvisioning: vi.fn(),
  };
});

vi.mock("@/context/AuthContext", () => ({
  useAuth: vi.fn(() => ({
    hasPermission: () => true,
    user: { user_id: "u1", username: "test", permissions: ["deployment:create"] },
    organizations: [],
  })),
}));

// Stub NodeShell and NodeLogs so their WS/polling logic doesn't run in jsdom
vi.mock("@/components/nodes/NodeShell", () => ({
  default: ({ nodeId }: { nodeId: string }) => (
    <div data-testid="node-shell-stub">NodeShell:{nodeId}</div>
  ),
}));

vi.mock("@/components/nodes/NodeLogs", () => ({
  default: ({ nodeId }: { nodeId: string }) => (
    <div data-testid="node-logs-stub">NodeLogs:{nodeId}</div>
  ),
}));

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const MOCK_NODE = {
  id: "n1",
  pool_id: "p1",
  node_name: "worker-1",
  agent_kind: "aws",
  provider: "aws",
  state: "provisioning",
  labels: {},
  advertise_url: null,
  expose_url: null,
  gpu_total: 1,
  gpu_allocated: 0,
  vcpu_total: 4,
  vcpu_allocated: 0,
  ram_gb_total: 16,
  ram_gb_allocated: 0,
  last_heartbeat: null,
  provider_instance_id: "i-abc123",
};

const MOCK_SUMMARY = {
  current_phase: "pulumi_up",
  terminal: false,
  phases: [
    { phase: "prepare", status: "succeeded", started_at: null, ended_at: null, last_message: null },
    { phase: "ami_lookup", status: "succeeded", started_at: null, ended_at: null, last_message: null },
    { phase: "pulumi_init", status: "succeeded", started_at: null, ended_at: null, last_message: null },
    { phase: "pulumi_up", status: "running", started_at: null, ended_at: null, last_message: "provisioning EC2" },
  ],
  attempt_count: 1,
  error: null,
  aws_metadata: {
    instance_class: "normal_gpu" as const,
    instance_type: "g5.xlarge",
    region: "us-east-1",
    ami_id: "ami-0abc123",
    instance_id: "i-abc123",
    public_dns: "ec2-1-2-3-4.compute-1.amazonaws.com",
  },
  job_id: "job-1",
};

const MOCK_SUMMARY_WITH_ERROR = {
  ...MOCK_SUMMARY,
  terminal: true,
  phases: [
    ...MOCK_SUMMARY.phases.slice(0, 3),
    { phase: "pulumi_up", status: "failed" as const, started_at: null, ended_at: null, last_message: "timeout" },
  ],
  error: {
    code: "PROVISION_FAILED",
    message: "EC2 provisioning timed out",
    hint: "Check AWS quotas",
    class: "ProvisionError",
  },
};

// ---------------------------------------------------------------------------
// Helper: render NodeDetail inside a MemoryRouter at the given path
// ---------------------------------------------------------------------------
function renderNodeDetail(
  initialPath = "/dashboard/compute/pools/p1/nodes/n1/provisioning",
) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        {/* mirrors App.tsx: compute/pools/:id/* → PoolDetail, but here we
            render NodeDetail directly because we only test node sub-routes */}
        <Route
          path="/dashboard/compute/pools/:id/nodes/:nid/*"
          element={<NodeDetail />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe("NodeDetail", () => {
  beforeEach(async () => {
    vi.clearAllMocks();

    // Re-establish default mock implementations after clearAllMocks wipes them
    const authCtx = await import("@/context/AuthContext");
    (authCtx.useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      hasPermission: () => true,
      user: { user_id: "u1", username: "test", permissions: ["deployment:create"] },
      organizations: [],
    });

    navigateSpy.mockReset();

    const nodeService = await import("@/services/nodeService");
    (nodeService.getNode as ReturnType<typeof vi.fn>).mockResolvedValue(MOCK_NODE);
    (nodeService.deleteNode as ReturnType<typeof vi.fn>).mockResolvedValue({
      terminating: false,
    });

    const provSvc = await import("@/services/provisioningService");
    (provSvc.getProvisioning as ReturnType<typeof vi.fn>).mockResolvedValue(MOCK_SUMMARY);
    (provSvc.retryProvisioning as ReturnType<typeof vi.fn>).mockResolvedValue({
      job_id: "job-2",
      phase: "prepare",
    });
  });

  // ── Regression guard: null-prop crash ─────────────────────────────────────
  it("renders provisioning phase content without crashing (null-prop regression guard)", async () => {
    renderNodeDetail();

    await waitFor(() => {
      // Header: node name appears in breadcrumb + h1
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    // Provisioning phases rendered (from ProvisioningStatus)
    await waitFor(() => {
      expect(screen.getByText("Provision EC2 instance")).toBeInTheDocument();
    });
  });

  // ── Loading state ──────────────────────────────────────────────────────────
  it("shows loading state initially", () => {
    renderNodeDetail();
    expect(screen.getByText("Loading node…")).toBeInTheDocument();
  });

  // ── Node metadata visible after load ──────────────────────────────────────
  it("shows node id and state badge after data loads", async () => {
    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getByText("n1")).toBeInTheDocument();
    });
    expect(screen.getByText("provisioning")).toBeInTheDocument();
    expect(screen.getByText("aws")).toBeInTheDocument();
  });

  // ── Tab switch: EC2 Details ────────────────────────────────────────────────
  it("navigates to EC2 Details tab and shows AWSMetadataGrid instance id", async () => {
    const user = userEvent.setup();
    renderNodeDetail();

    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    // The EC2 Details tab link should be present (aws node)
    const ec2Tab = await waitFor(() => screen.getByText("EC2 Details"));
    await user.click(ec2Tab);

    // After navigation, AWSMetadataGrid should show the instance id
    await waitFor(() => {
      expect(screen.getByText("i-abc123")).toBeInTheDocument();
    });
  });

  // ── EC2 tab hidden for non-aws nodes ───────────────────────────────────────
  it("does not show EC2 Details tab for non-aws nodes", async () => {
    const nodeService = await import("@/services/nodeService");
    (nodeService.getNode as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...MOCK_NODE,
      provider: "nosana",
    });

    renderNodeDetail();

    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    // EC2 Details tab should not appear
    expect(screen.queryByText("EC2 Details")).not.toBeInTheDocument();
  });

  // ── EC2 tab: missing aws_metadata shows placeholder ───────────────────────
  it("shows placeholder when aws_metadata is missing", async () => {
    const provSvc = await import("@/services/provisioningService");
    (provSvc.getProvisioning as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...MOCK_SUMMARY,
      aws_metadata: null,
    });

    renderNodeDetail("/dashboard/compute/pools/p1/nodes/n1/ec2");
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    await waitFor(() => {
      expect(
        screen.getByText(/EC2 details not available yet/i),
      ).toBeInTheDocument();
    });
  });

  // ── Shell tab ─────────────────────────────────────────────────────────────
  it("shows NodeShell stub on shell tab", async () => {
    const user = userEvent.setup();
    renderNodeDetail();

    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    const shellTab = await waitFor(() => screen.getByText("Shell"));
    await user.click(shellTab);

    await waitFor(() => {
      expect(screen.getByTestId("node-shell-stub")).toBeInTheDocument();
    });
  });

  // ── Logs tab ──────────────────────────────────────────────────────────────
  it("shows NodeLogs stub on logs tab", async () => {
    const user = userEvent.setup();
    renderNodeDetail();

    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    const logsTab = await waitFor(() => screen.getByText("Logs"));
    await user.click(logsTab);

    await waitFor(() => {
      expect(screen.getByTestId("node-logs-stub")).toBeInTheDocument();
    });
  });

  // ── Retry button: shown when error present + permission granted ───────────
  it("shows Retry button when summary.error is set and user has deployment:create", async () => {
    const provSvc = await import("@/services/provisioningService");
    (provSvc.getProvisioning as ReturnType<typeof vi.fn>).mockResolvedValue(
      MOCK_SUMMARY_WITH_ERROR,
    );

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    await waitFor(() => {
      expect(screen.getByTestId("retry-provisioning-btn")).toBeInTheDocument();
    });
  });

  // ── Retry button: hidden when no error ────────────────────────────────────
  it("does NOT show Retry button when summary.error is null", async () => {
    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    // Let the async load settle by waiting for the phase list content
    await waitFor(() => {
      expect(screen.getByText("Provision EC2 instance")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("retry-provisioning-btn"),
    ).not.toBeInTheDocument();
  });

  // ── Retry button: hidden when user lacks permission ────────────────────────
  it("does NOT show Retry button when user lacks deployment:create", async () => {
    const authCtx = await import("@/context/AuthContext");
    (authCtx.useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      hasPermission: () => false,
      user: { user_id: "u1", username: "test", permissions: [] },
      organizations: [],
    });

    const provSvc = await import("@/services/provisioningService");
    (provSvc.getProvisioning as ReturnType<typeof vi.fn>).mockResolvedValue(
      MOCK_SUMMARY_WITH_ERROR,
    );

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    // Wait for phase content — confirms the failed phases are rendered
    await waitFor(() => {
      expect(screen.getByText("Provision EC2 instance")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("retry-provisioning-btn"),
    ).not.toBeInTheDocument();
  });

  // ── Retry button: calls retryProvisioning on click ────────────────────────
  it("calls retryProvisioning when Retry button is clicked", async () => {
    const user = userEvent.setup();

    const provSvc = await import("@/services/provisioningService");
    (provSvc.getProvisioning as ReturnType<typeof vi.fn>).mockResolvedValue(
      MOCK_SUMMARY_WITH_ERROR,
    );
    const retrySpy = provSvc.retryProvisioning as ReturnType<typeof vi.fn>;

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    const retryBtn = await waitFor(() => screen.getByTestId("retry-provisioning-btn"));
    await user.click(retryBtn);

    expect(retrySpy).toHaveBeenCalledWith("n1");
  });

  // ── Attempt badge shown when attempt_count > 1 ────────────────────────────
  it("shows attempt badge when attempt_count > 1", async () => {
    const provSvc = await import("@/services/provisioningService");
    (provSvc.getProvisioning as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...MOCK_SUMMARY,
      attempt_count: 3,
    });

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    await waitFor(() => {
      expect(screen.getByTestId("provisioning-attempt-badge")).toBeInTheDocument();
    });
  });

  // ── Polling stops when terminal ────────────────────────────────────────────
  it("stops polling when summary.terminal is true", async () => {
    const provSvc = await import("@/services/provisioningService");
    const getProvSpy = provSvc.getProvisioning as ReturnType<typeof vi.fn>;
    // Return terminal=true so polling should not start
    getProvSpy.mockResolvedValue({ ...MOCK_SUMMARY, terminal: true });

    renderNodeDetail();

    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    // Record call count right after render (initial load only)
    const callCountAfterLoad = getProvSpy.mock.calls.length;

    // Use real timers here — just wait a bit longer than one poll interval
    // to confirm no extra calls arrive
    await new Promise<void>((resolve) => setTimeout(resolve, 50));

    // No additional getProvisioning calls should have fired
    expect(getProvSpy.mock.calls.length).toBe(callCountAfterLoad);
  });

  // ── Error state ────────────────────────────────────────────────────────────
  it("shows error state when getNode fails", async () => {
    const nodeService = await import("@/services/nodeService");
    (nodeService.getNode as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("network error"),
    );

    renderNodeDetail();

    await waitFor(() => {
      expect(
        screen.getByText("Failed to load node details."),
      ).toBeInTheDocument();
    });
  });

  // ── Back link ─────────────────────────────────────────────────────────────
  it("renders a back link to the pool", async () => {
    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    const backLink = screen.getByText("← Back to pool");
    expect(backLink.closest("a")).toHaveAttribute(
      "href",
      "/dashboard/compute/pools/p1",
    );
  });

  // ── Delete Node: button shown when user has deployment:delete ─────────────
  it("shows the Delete Node button when user has deployment:delete", async () => {
    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });
    expect(screen.getByTestId("delete-node-btn")).toBeInTheDocument();
  });

  // ── Delete Node: button hidden when user lacks deployment:delete ──────────
  it("does NOT show the Delete Node button when user lacks deployment:delete", async () => {
    const authCtx = await import("@/context/AuthContext");
    (authCtx.useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      hasPermission: () => false,
      user: { user_id: "u1", username: "test", permissions: [] },
      organizations: [],
    });

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });
    expect(screen.queryByTestId("delete-node-btn")).not.toBeInTheDocument();
  });

  // ── Delete Node: confirm true → deleteNode called, terminating toast, nav ──
  it("calls deleteNode and shows termination toast + navigates on a 202 (terminating)", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const nodeService = await import("@/services/nodeService");
    const delSpy = nodeService.deleteNode as ReturnType<typeof vi.fn>;
    delSpy.mockResolvedValue({
      terminating: true,
      state: "terminating",
      nodeId: "n1",
    });

    const { toast } = await import("sonner");

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    await user.click(screen.getByTestId("delete-node-btn"));

    await waitFor(() => {
      expect(delSpy).toHaveBeenCalledWith("n1");
    });
    expect(toast.success).toHaveBeenCalledWith(
      "Termination started — destroying the EC2 instance…",
    );
    await waitFor(() => {
      expect(navigateSpy).toHaveBeenCalledWith("/dashboard/compute/pools/p1");
    });

    confirmSpy.mockRestore();
  });

  // ── Delete Node: confirm true + 204 → "Node deleted" toast ────────────────
  it("shows the plain 'Node deleted' toast on a 204 (terminating false)", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const nodeService = await import("@/services/nodeService");
    (nodeService.deleteNode as ReturnType<typeof vi.fn>).mockResolvedValue({
      terminating: false,
    });

    const { toast } = await import("sonner");

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    await user.click(screen.getByTestId("delete-node-btn"));

    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("Node deleted");
    });
    confirmSpy.mockRestore();
  });

  // ── Delete Node: confirm cancelled → no deleteNode call ───────────────────
  it("does not call deleteNode when the confirm dialog is dismissed", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    const nodeService = await import("@/services/nodeService");
    const delSpy = nodeService.deleteNode as ReturnType<typeof vi.fn>;

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    await user.click(screen.getByTestId("delete-node-btn"));

    expect(delSpy).not.toHaveBeenCalled();
    expect(navigateSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  // ── Delete Node: 409 → shows the conflict detail toast, no navigation ─────
  it("shows the 409 conflict detail toast and does not navigate", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const nodeService = await import("@/services/nodeService");
    (nodeService.deleteNode as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: { status: 409, data: { detail: "node has active deployments" } },
    });

    const { toast } = await import("sonner");

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    await user.click(screen.getByTestId("delete-node-btn"));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("node has active deployments");
    });
    expect(navigateSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  // ── Delete Node: 409 with no detail → fallback conflict message ───────────
  it("falls back to the default conflict message when 409 has no detail", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const nodeService = await import("@/services/nodeService");
    (nodeService.deleteNode as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: { status: 409, data: {} },
    });

    const { toast } = await import("sonner");

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    await user.click(screen.getByTestId("delete-node-btn"));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        "Cannot delete: stop active deployments on this node first.",
      );
    });
    confirmSpy.mockRestore();
  });

  // ── Delete Node: generic (non-409) failure → generic error toast ──────────
  it("shows a generic error toast on a non-409 delete failure", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const nodeService = await import("@/services/nodeService");
    (nodeService.deleteNode as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: { status: 500, data: {} },
    });

    const { toast } = await import("sonner");

    renderNodeDetail();
    await waitFor(() => {
      expect(screen.getAllByText("worker-1").length).toBeGreaterThan(0);
    });

    await user.click(screen.getByTestId("delete-node-btn"));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Failed to delete node");
    });
    expect(navigateSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
