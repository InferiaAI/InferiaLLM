import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import PoolDetail from "./PoolDetail";

// Stub NodeDetail so clicking into a node route renders a recognisable marker
// instead of the real NodeDetail (which would need extra service mocks).
vi.mock("./NodeDetail", () => ({
  default: () => <div data-testid="node-detail-stub">NodeDetail</div>,
}));

// ---------------------------------------------------------------------------
// Mocks — factories must not reference top-level variables (hoisting)
// ---------------------------------------------------------------------------

vi.mock("@/services/poolService", () => ({
  getPool: vi.fn(),
  deletePool: vi.fn(),
}));

vi.mock("@/services/nodeService", () => ({
  listNodes: vi.fn(),
  deleteNode: vi.fn(),
}));

vi.mock("@/services/workerService", () => ({
  listWorkers: vi.fn(),
  revokeWorker: vi.fn(),
}));

vi.mock("@/components/workers/AddWorkerModal", () => ({
  default: ({ poolId }: { poolId: string }) => (
    <div data-testid="add-worker-modal">{poolId}</div>
  ),
}));

vi.mock("@/lib/api", () => ({
  computeApi: {
    get: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/context/AuthContext", () => ({
  useAuth: vi.fn(() => ({
    hasPermission: () => true,
    user: { org_id: "org-1", user_id: "u1", username: "test", permissions: [] },
    organizations: [],
  })),
}));

vi.mock("@/components/nodes/NodeLogs", () => ({
  default: () => <div>NodeLogs</div>,
}));
vi.mock("@/components/nodes/NodeShell", () => ({
  default: () => <div>NodeShell</div>,
}));
vi.mock("@/components/nodes/ProvisioningStatus", () => ({
  default: () => <div>ProvisioningStatus</div>,
}));

// ---------------------------------------------------------------------------
// Fixtures (declared after mocks)
// ---------------------------------------------------------------------------

const MOCK_NODE: import("@/services/nodeService").NodeView = {
  id: "node-abc",
  pool_id: "pool-123",
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

const MOCK_POOL = {
  pool_id: "pool-123",
  pool_name: "test-pool",
  provider: "aws",
  pool_type: "gpu",
  gpu_count: 1,
  allowed_gpu_types: ["A10G"],
  lifecycle_state: "active",
  is_active: true,
  owner_type: "user",
  owner_id: "org-1",
  max_cost_per_hour: 2.0,
  is_dedicated: false,
  scheduling_policy_json: "{}",
  provider_pool_id: "aws/g5.xlarge",
  provider_credential_name: "default",
  cluster_id: "",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderPoolDetail() {
  return render(
    <MemoryRouter initialEntries={["/dashboard/compute/pools/pool-123"]}>
      <Routes>
        <Route path="/dashboard/compute/pools/:id/*" element={<PoolDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function waitForPoolLoad() {
  // Pool name appears in both breadcrumb and h1, so use getAllByText
  await waitFor(() => {
    expect(screen.getAllByText("test-pool").length).toBeGreaterThan(0);
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PoolDetail", () => {
  beforeEach(async () => {
    vi.clearAllMocks();

    // Re-establish the default permissive auth after clearAllMocks wipes it
    const authCtx = await import("@/context/AuthContext");
    (authCtx.useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      hasPermission: () => true,
      user: { org_id: "org-1", user_id: "u1", username: "test", permissions: [] },
      organizations: [],
    });

    const poolService = await import("@/services/poolService");
    (poolService.getPool as ReturnType<typeof vi.fn>).mockResolvedValue(MOCK_POOL);
    (poolService.deletePool as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);

    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (nodeService.deleteNode as ReturnType<typeof vi.fn>).mockResolvedValue({
      terminating: false,
    });

    const workerService = await import("@/services/workerService");
    (workerService.listWorkers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (workerService.revokeWorker as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);

    const api = await import("@/lib/api");
    (api.computeApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { deployments: [] } });
  });

  it("renders pool name on load", async () => {
    renderPoolDetail();
    await waitForPoolLoad();
    expect(screen.getAllByText("test-pool").length).toBeGreaterThan(0);
  });

  it("clicking Nodes tab shows the nodes tab content", async () => {
    const user = userEvent.setup();
    renderPoolDetail();
    await waitForPoolLoad();
    await user.click(screen.getByRole("button", { name: "Nodes" }));
    expect(screen.getByText("No nodes in this pool yet.")).toBeInTheDocument();
  });

  it("clicking Deployments tab shows the deployments table", async () => {
    const user = userEvent.setup();
    renderPoolDetail();
    await waitForPoolLoad();
    await user.click(screen.getByRole("button", { name: "Deployments" }));
    expect(
      screen.getByText("No deployments on this pool."),
    ).toBeInTheDocument();
  });

  it("clicking Settings tab shows settings and danger zone", async () => {
    const user = userEvent.setup();
    renderPoolDetail();
    await waitForPoolLoad();
    await user.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByText("Delete Pool")).toBeInTheDocument();
  });

  it("node row links navigate to node-detail provisioning route", async () => {
    const user = userEvent.setup();
    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockResolvedValue([MOCK_NODE]);

    renderPoolDetail();
    await waitForPoolLoad();

    // Switch to Nodes tab
    await user.click(screen.getByRole("button", { name: "Nodes" }));

    // Node name link should be present
    await waitFor(() => {
      expect(screen.getByText("worker-1")).toBeInTheDocument();
    });

    // The node name cell is a Link to the provisioning route
    const nodeLink = screen.getByText("worker-1").closest("a");
    expect(nodeLink).toHaveAttribute(
      "href",
      "/dashboard/compute/pools/pool-123/nodes/node-abc/provisioning",
    );

    // Quick-action "Status" link also points to provisioning
    const statusLink = screen.getByText("Status").closest("a");
    expect(statusLink).toHaveAttribute(
      "href",
      "/dashboard/compute/pools/pool-123/nodes/node-abc/provisioning",
    );
  });

  it("deep-link to node sub-route renders NodeDetail without showing PoolDetail tabs", async () => {
    render(
      <MemoryRouter
        initialEntries={["/dashboard/compute/pools/pool-123/nodes/node-abc/shell"]}
      >
        <Routes>
          <Route
            path="/dashboard/compute/pools/:id/*"
            element={<PoolDetail />}
          />
        </Routes>
      </MemoryRouter>,
    );

    // When the URL is a node sub-route, PoolDetail renders NodeDetail as a
    // takeover — we should see our stub but NOT the pool tab buttons.
    await waitFor(() => {
      expect(screen.getByTestId("node-detail-stub")).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "Overview" })).not.toBeInTheDocument();
  });

  // ── Per-row Delete: shown when permitted ──────────────────────────────────
  it("shows a per-row Delete action in the Nodes table when permitted", async () => {
    const user = userEvent.setup();
    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockResolvedValue([MOCK_NODE]);

    renderPoolDetail();
    await waitForPoolLoad();
    await user.click(screen.getByRole("button", { name: "Nodes" }));

    await waitFor(() => {
      expect(screen.getByTestId("delete-node-node-abc")).toBeInTheDocument();
    });
  });

  // ── Per-row Delete: hidden when user lacks deployment:delete ───────────────
  it("hides the per-row Delete action when user lacks deployment:delete", async () => {
    const authCtx = await import("@/context/AuthContext");
    (authCtx.useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      hasPermission: () => false,
      user: { org_id: "org-1", user_id: "u1", username: "test", permissions: [] },
      organizations: [],
    });

    const user = userEvent.setup();
    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockResolvedValue([MOCK_NODE]);

    renderPoolDetail();
    await waitForPoolLoad();
    await user.click(screen.getByRole("button", { name: "Nodes" }));

    await waitFor(() => {
      expect(screen.getByText("worker-1")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("delete-node-node-abc")).not.toBeInTheDocument();
  });

  // ── Per-row Delete: confirm true → deleteNode called + node list refetched ─
  it("calls deleteNode then refetches the node list on a confirmed row delete", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const nodeService = await import("@/services/nodeService");
    const listSpy = nodeService.listNodes as ReturnType<typeof vi.fn>;
    listSpy.mockResolvedValue([MOCK_NODE]);
    const delSpy = nodeService.deleteNode as ReturnType<typeof vi.fn>;
    delSpy.mockResolvedValue({ terminating: true, state: "terminating", nodeId: "node-abc" });

    const { toast } = await import("sonner");

    renderPoolDetail();
    await waitForPoolLoad();
    await user.click(screen.getByRole("button", { name: "Nodes" }));

    await waitFor(() => {
      expect(screen.getByTestId("delete-node-node-abc")).toBeInTheDocument();
    });

    const callsBefore = listSpy.mock.calls.length;
    await user.click(screen.getByTestId("delete-node-node-abc"));

    await waitFor(() => {
      expect(delSpy).toHaveBeenCalledWith("node-abc");
    });
    expect(toast.success).toHaveBeenCalledWith(
      "Termination started — destroying the EC2 instance…",
    );
    // onRefetch (fetchNodes) is invoked after a successful delete
    await waitFor(() => {
      expect(listSpy.mock.calls.length).toBeGreaterThan(callsBefore);
    });
    confirmSpy.mockRestore();
  });

  // ── Per-row Delete: confirm cancelled → no deleteNode call ─────────────────
  it("does not call deleteNode when the row delete confirm is dismissed", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockResolvedValue([MOCK_NODE]);
    const delSpy = nodeService.deleteNode as ReturnType<typeof vi.fn>;

    renderPoolDetail();
    await waitForPoolLoad();
    await user.click(screen.getByRole("button", { name: "Nodes" }));

    await waitFor(() => {
      expect(screen.getByTestId("delete-node-node-abc")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("delete-node-node-abc"));
    expect(delSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  // ── Per-row Delete: 409 → conflict detail toast ────────────────────────────
  it("shows the 409 conflict detail toast on a failed row delete", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockResolvedValue([MOCK_NODE]);
    (nodeService.deleteNode as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: { status: 409, data: { detail: "node still busy" } },
    });

    const { toast } = await import("sonner");

    renderPoolDetail();
    await waitForPoolLoad();
    await user.click(screen.getByRole("button", { name: "Nodes" }));

    await waitFor(() => {
      expect(screen.getByTestId("delete-node-node-abc")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("delete-node-node-abc"));
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("node still busy");
    });
    confirmSpy.mockRestore();
  });

  // ── Per-row Delete: generic failure → generic error toast ──────────────────
  it("shows a generic error toast on a non-409 row delete failure", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockResolvedValue([MOCK_NODE]);
    (nodeService.deleteNode as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: { status: 500, data: {} },
    });

    const { toast } = await import("sonner");

    renderPoolDetail();
    await waitForPoolLoad();
    await user.click(screen.getByRole("button", { name: "Nodes" }));

    await waitFor(() => {
      expect(screen.getByTestId("delete-node-node-abc")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("delete-node-node-abc"));
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Failed to delete node");
    });
    confirmSpy.mockRestore();
  });

  // ── fetchNodes error surfacing: toasts once on failure ─────────────────────
  it("toasts 'Failed to load nodes' when the node list fetch fails", async () => {
    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("network down"),
    );

    const { toast } = await import("sonner");

    renderPoolDetail();
    await waitForPoolLoad();

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Failed to load nodes");
    });
  });

  // ── fetchNodes error surfacing: empty pool does NOT toast ──────────────────
  it("does not toast on a successful but empty node list", async () => {
    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    const { toast } = await import("sonner");

    renderPoolDetail();
    await waitForPoolLoad();

    // give the initial fetch a tick to settle
    await new Promise<void>((resolve) => setTimeout(resolve, 20));
    expect(toast.error).not.toHaveBeenCalledWith("Failed to load nodes");
  });
});

// ---------------------------------------------------------------------------
// Workers tab
// ---------------------------------------------------------------------------

const WORKER_POOL = { ...MOCK_POOL, provider: "on_prem", provider_pool_id: "worker:test-pool" };

const MOCK_WORKER: import("@/services/workerService").WorkerView = {
  node_id: "worker-node-1",
  node_name: "gpu-host-1",
  advertise_url: "http://gpu-host-1:8080",
  agent_kind: "worker",
  state: "ready",
  connected: true,
  last_heartbeat: "2026-01-01T00:00:00Z",
  used: {},
  loaded_models: ["qwen3:4b"],
  allocatable: {},
};

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/dashboard/compute/pools/:id/*" element={<PoolDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function useWorkerPool() {
  const poolService = await import("@/services/poolService");
  (poolService.getPool as ReturnType<typeof vi.fn>).mockResolvedValue(WORKER_POOL);
}

describe("PoolDetail — Workers tab", () => {
  // This block has its own setup: a describe does not inherit the beforeEach of
  // its sibling, and leaning on leaked mock state makes the order significant.
  beforeEach(async () => {
    vi.clearAllMocks();

    const authCtx = await import("@/context/AuthContext");
    (authCtx.useAuth as ReturnType<typeof vi.fn>).mockReturnValue({
      hasPermission: () => true,
      user: { org_id: "org-1", user_id: "u1", username: "test", permissions: [] },
      organizations: [],
    });

    const poolService = await import("@/services/poolService");
    (poolService.getPool as ReturnType<typeof vi.fn>).mockResolvedValue(MOCK_POOL);

    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    const workerService = await import("@/services/workerService");
    (workerService.listWorkers as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (workerService.revokeWorker as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);

    const api = await import("@/lib/api");
    (api.computeApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { deployments: [] },
    });
  });

  it("is hidden on a pool whose provider creates its own nodes", async () => {
    renderPoolDetail();
    await waitForPoolLoad();
    expect(screen.queryByRole("button", { name: "Workers" })).not.toBeInTheDocument();
  });

  it("does not poll the worker endpoint for such a pool", async () => {
    const { listWorkers } = await import("@/services/workerService");

    renderPoolDetail();
    await waitForPoolLoad();
    await new Promise<void>((resolve) => setTimeout(resolve, 20));

    // Polling every pool every 15s also toasts on every failure, so an
    // ungated fetch here is a user-visible bug, not just a wasted request.
    expect(listWorkers).not.toHaveBeenCalled();
  });

  it("appears on a self-hosted pool", async () => {
    await useWorkerPool();
    renderPoolDetail();
    await waitForPoolLoad();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Workers" })).toBeInTheDocument();
    });
  });

  it("tells you how to get a worker when the pool is empty", async () => {
    await useWorkerPool();
    renderPoolDetail();
    await waitForPoolLoad();

    await userEvent.click(await screen.findByRole("button", { name: "Workers" }));
    expect(await screen.findByText(/No workers yet/)).toBeInTheDocument();
  });

  it("lists a registered worker", async () => {
    await useWorkerPool();
    const { listWorkers } = await import("@/services/workerService");
    (listWorkers as ReturnType<typeof vi.fn>).mockResolvedValue([MOCK_WORKER]);

    renderPoolDetail();
    await waitForPoolLoad();
    await userEvent.click(await screen.findByRole("button", { name: "Workers" }));

    expect(await screen.findByText("gpu-host-1")).toBeInTheDocument();
    expect(screen.getByText("online")).toBeInTheDocument();
    expect(screen.getByText("qwen3:4b")).toBeInTheDocument();
  });

  it("opens the mint modal from Add worker", async () => {
    await useWorkerPool();
    renderPoolDetail();
    await waitForPoolLoad();

    await userEvent.click(await screen.findByRole("button", { name: "Workers" }));
    await userEvent.click(screen.getByRole("button", { name: "Add worker" }));

    expect(await screen.findByTestId("add-worker-modal")).toHaveTextContent("pool-123");
  });

  it("revokes a worker and refetches", async () => {
    await useWorkerPool();
    const { listWorkers, revokeWorker } = await import("@/services/workerService");
    (listWorkers as ReturnType<typeof vi.fn>).mockResolvedValue([MOCK_WORKER]);

    renderPoolDetail();
    await waitForPoolLoad();
    await userEvent.click(await screen.findByRole("button", { name: "Workers" }));
    await userEvent.click(await screen.findByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(revokeWorker).toHaveBeenCalledWith("worker-node-1"));
  });

  it("offers no Revoke on an already-revoked worker", async () => {
    await useWorkerPool();
    const { listWorkers } = await import("@/services/workerService");
    (listWorkers as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...MOCK_WORKER, state: "terminated", connected: false },
    ]);

    renderPoolDetail();
    await waitForPoolLoad();
    await userEvent.click(await screen.findByRole("button", { name: "Workers" }));

    expect(await screen.findByText("gpu-host-1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
  });

  it("opens on the Workers tab when linked to directly", async () => {
    // Creating a pool navigates straight here, so the deep link has to select
    // the tab rather than falling back to Overview.
    await useWorkerPool();
    renderAt("/dashboard/compute/pools/pool-123/workers");
    await waitForPoolLoad();

    // The pool load and the worker fetch each re-render the panel, so re-query
    // rather than holding on to an element from an earlier pass.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Add worker" })).toBeInTheDocument();
      expect(screen.getByText(/No workers yet/)).toBeInTheDocument();
    });
  });
});
