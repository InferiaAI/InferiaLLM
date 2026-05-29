import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import PoolDetail from "./PoolDetail";

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

vi.mock("@/services/poolService", () => ({
  getPool: vi.fn().mockResolvedValue(MOCK_POOL),
  deletePool: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/services/nodeService", () => ({
  listNodes: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/lib/api", () => ({
  computeApi: {
    get: vi.fn().mockResolvedValue({ data: { deployments: [] } }),
    delete: vi.fn().mockResolvedValue({}),
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

function renderPoolDetail() {
  return render(
    <MemoryRouter initialEntries={["/dashboard/compute/pools/pool-123"]}>
      <Routes>
        <Route path="/dashboard/compute/pools/:id/*" element={<PoolDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("PoolDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Re-set the mock after clearAllMocks
    const poolService = require("@/services/poolService");
    poolService.getPool.mockResolvedValue(MOCK_POOL);
    const nodeService = require("@/services/nodeService");
    nodeService.listNodes.mockResolvedValue([]);
    const api = require("@/lib/api");
    api.computeApi.get.mockResolvedValue({ data: { deployments: [] } });
  });

  it("renders pool name on load", async () => {
    renderPoolDetail();
    await waitFor(() => {
      expect(screen.getByText("test-pool")).toBeInTheDocument();
    });
  });

  it("clicking Nodes tab shows the nodes tab content", async () => {
    const user = userEvent.setup();
    renderPoolDetail();
    await waitFor(() =>
      expect(screen.getByText("test-pool")).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Nodes" }));
    expect(screen.getByText("No nodes in this pool yet.")).toBeInTheDocument();
  });

  it("clicking Deployments tab shows the deployments table", async () => {
    const user = userEvent.setup();
    renderPoolDetail();
    await waitFor(() =>
      expect(screen.getByText("test-pool")).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Deployments" }));
    expect(
      screen.getByText("No deployments on this pool."),
    ).toBeInTheDocument();
  });

  it("clicking Settings tab shows settings and danger zone", async () => {
    const user = userEvent.setup();
    renderPoolDetail();
    await waitFor(() =>
      expect(screen.getByText("test-pool")).toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByText("Delete Pool")).toBeInTheDocument();
  });
});
