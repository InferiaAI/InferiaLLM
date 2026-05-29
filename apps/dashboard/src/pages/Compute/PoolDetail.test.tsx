import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import PoolDetail from "./PoolDetail";

// ---------------------------------------------------------------------------
// Mocks — factories must not reference top-level variables (hoisting)
// ---------------------------------------------------------------------------

vi.mock("@/services/poolService", () => ({
  getPool: vi.fn(),
  deletePool: vi.fn(),
}));

vi.mock("@/services/nodeService", () => ({
  listNodes: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  computeApi: {
    get: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({
    hasPermission: () => true,
    user: { org_id: "org-1", user_id: "u1", username: "test", permissions: [] },
    organizations: [],
  }),
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
// Fixture (declared after mocks)
// ---------------------------------------------------------------------------

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
    const poolService = await import("@/services/poolService");
    (poolService.getPool as ReturnType<typeof vi.fn>).mockResolvedValue(MOCK_POOL);
    (poolService.deletePool as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);

    const nodeService = await import("@/services/nodeService");
    (nodeService.listNodes as ReturnType<typeof vi.fn>).mockResolvedValue([]);

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
});
