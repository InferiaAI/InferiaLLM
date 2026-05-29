import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Pools from "./Pools";

// Mock poolService
vi.mock("@/services/poolService", () => ({
  listPools: vi.fn(),
}));

// Mock auth
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({
    hasPermission: () => true,
    user: { org_id: "org-1", user_id: "u1", username: "test", permissions: [] },
    organizations: [],
  }),
}));

const MOCK_POOLS = [
  {
    pool_id: "pool-abc-123",
    pool_name: "my-gpu-pool",
    provider: "aws",
    pool_type: "gpu",
    gpu_count: 1,
    allowed_gpu_types: ["A10G"],
    lifecycle_state: "active",
    is_active: true,
    owner_type: "user",
    owner_id: "org-1",
    max_cost_per_hour: 1.5,
    is_dedicated: false,
    scheduling_policy_json: "{}",
    provider_pool_id: "aws/g5.xlarge",
    provider_credential_name: "default",
    cluster_id: "",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

describe("Pools", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders pool rows when listPools returns data", async () => {
    const { listPools } = await import("@/services/poolService");
    (listPools as ReturnType<typeof vi.fn>).mockResolvedValue(MOCK_POOLS);

    render(
      <MemoryRouter>
        <Pools />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("my-gpu-pool")).toBeInTheDocument();
    });
    expect(screen.getByText("aws")).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
  });

  it("shows empty-state CTA when listPools returns empty array", async () => {
    const { listPools } = await import("@/services/poolService");
    (listPools as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    render(
      <MemoryRouter>
        <Pools />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Create your first pool")).toBeInTheDocument();
    });
  });
});
