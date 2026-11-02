import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  computeApi: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({
    user: { org_id: "org-1" },
    organizations: [],
    hasPermission: () => true,
  }),
}));

import { computeApi } from "@/lib/api";
import Deployments from "./Deployments";

const api = computeApi as unknown as Record<"get" | "post" | "delete", ReturnType<typeof vi.fn>>;

function renderWithState(state: string) {
  api.get.mockResolvedValue({
    data: { deployments: [{ deployment_id: "dep-1", model_name: "qwen", state }] },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Deployments />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  api.post.mockResolvedValue({ data: {} });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("Deployments actions", () => {
  it("offers Stop for a deployment waiting on its node, and stops it", async () => {
    renderWithState("PENDING_NODE");
    const [stop] = await screen.findAllByRole("button", { name: "Stop" });
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();

    await userEvent.click(stop);

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/deployment/terminate", { deployment_id: "dep-1" }));
  });

  it("offers Start and Delete, not Stop, once a deployment has ended", async () => {
    renderWithState("STOPPED");
    await screen.findAllByRole("button", { name: "Start" });
    expect(screen.getAllByRole("button", { name: "Delete" }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });
});
