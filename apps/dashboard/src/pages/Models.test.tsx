import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

// ── Mock services ──────────────────────────────────────────────────────────────

vi.mock("@/services/modelService", () => ({
  listModels: vi.fn(),
  addModel: vi.fn(),
  deleteModel: vi.fn(),
}));

vi.mock("@/services/huggingfaceService", () => ({
  searchHFModels: vi.fn(),
}));

// ── Mock AuthContext so we can control permissions ─────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-unused-vars
const mockHasPermission = vi.fn((_perm: string) => true);

vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({
    user: {
      user_id: "u1",
      username: "testuser",
      email: "test@example.com",
      roles: ["owner"],
      permissions: ["model:list", "model:add", "model:delete"],
      org_id: "org1",
      totp_enabled: true,
    },
    isLoading: false,
    isAuthenticated: true,
    logout: vi.fn(),
    refreshUser: vi.fn(),
    organizations: [],
    hasPermission: mockHasPermission,
  }),
}));

// ── Import after mocks ─────────────────────────────────────────────────────────

import Models from "@/pages/Models";
import { listModels, addModel, deleteModel } from "@/services/modelService";
import { searchHFModels } from "@/services/huggingfaceService";
import type { CachedModel } from "@/services/modelService";

// ── Test helpers ───────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        // Disable automatic refetching in tests
        refetchOnWindowFocus: false,
        staleTime: Infinity,
      },
    },
  });
}

function renderModels(queryClient?: QueryClient) {
  const qc = queryClient ?? makeQueryClient();
  return {
    qc,
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <Models />
        </MemoryRouter>
      </QueryClientProvider>
    ),
  };
}

const MOCK_CACHED_MODELS: CachedModel[] = [
  {
    id: "m1",
    source: "hf",
    model_id: "meta-llama/Llama-3-8B",
    revision: "abc1234",
    status: "cached",
    bytes_total: 8_000_000_000,
    bytes_done: 8_000_000_000,
    engine_hint: "vllm",
  },
  {
    id: "m2",
    source: "hf",
    model_id: "mistralai/Mistral-7B-v0.1",
    revision: "def5678",
    status: "downloading",
    bytes_total: 7 * 1024 * 1024 * 1024,  // 7.0 GB exactly
    bytes_done: Math.round(3.5 * 1024 * 1024 * 1024),  // 3.5 GB exactly
    engine_hint: "vllm",
  },
];

// ── Tests ──────────────────────────────────────────────────────────────────────

describe("Models page — cached list", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listModels).mockResolvedValue(MOCK_CACHED_MODELS);
    vi.mocked(searchHFModels).mockResolvedValue([]);
    vi.mocked(addModel).mockResolvedValue({ id: "new-id" });
    vi.mocked(deleteModel).mockResolvedValue(undefined);
    mockHasPermission.mockImplementation(() => true);
  });

  it("renders cached model rows including model_id and status", async () => {
    renderModels();
    await waitFor(() => {
      expect(screen.getByText("meta-llama/Llama-3-8B")).toBeInTheDocument();
    });
    expect(screen.getByText("mistralai/Mistral-7B-v0.1")).toBeInTheDocument();
    // Status badges
    expect(screen.getByText("cached")).toBeInTheDocument();
    expect(screen.getByText("downloading")).toBeInTheDocument();
  });

  it("shows a progress bar for a downloading row", async () => {
    renderModels();
    await waitFor(() => {
      expect(screen.getByText("mistralai/Mistral-7B-v0.1")).toBeInTheDocument();
    });
    // Progress percentage: 3.5GB / 7GB = 50%
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("shows human-readable sizes for a downloading row", async () => {
    renderModels();
    await waitFor(() => {
      expect(screen.getByText("mistralai/Mistral-7B-v0.1")).toBeInTheDocument();
    });
    // The size span renders: "3.5 GB" + " / " + "7.0 GB" as text nodes.
    // Match the <span> whose direct textContent contains both.
    const sizeSpan = screen.getAllByText((_, el) => {
      if (!el || el.tagName !== "SPAN") return false;
      const t = el.textContent ?? "";
      return t.includes("3.5 GB") && t.includes("7.0 GB");
    });
    expect(sizeSpan.length).toBeGreaterThan(0);
  });

  it("shows error text for error-status rows", async () => {
    const errModel: CachedModel = {
      id: "m3",
      source: "hf",
      model_id: "bad/model",
      revision: "",
      status: "error",
      bytes_total: 0,
      bytes_done: 0,
      error: "Download failed: 404 Not Found",
    };
    vi.mocked(listModels).mockResolvedValue([errModel]);
    renderModels();

    await waitFor(() => {
      expect(screen.getByText("bad/model")).toBeInTheDocument();
    });
    expect(screen.getByText("Download failed: 404 Not Found")).toBeInTheDocument();
  });

  it("shows empty state when there are no cached models", async () => {
    vi.mocked(listModels).mockResolvedValue([]);
    renderModels();

    await waitFor(() => {
      expect(screen.getByText(/No cached models yet/)).toBeInTheDocument();
    });
  });

  it("does not render rows while loading", () => {
    vi.mocked(listModels).mockReturnValue(new Promise(() => {}));
    renderModels();
    expect(screen.queryByText("meta-llama/Llama-3-8B")).not.toBeInTheDocument();
  });

  it("calls listModels on mount", async () => {
    renderModels();
    await waitFor(() => {
      expect(listModels).toHaveBeenCalled();
    });
  });
});

describe("Models page — Add Model link", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listModels).mockResolvedValue([]);
    vi.mocked(searchHFModels).mockResolvedValue([]);
    mockHasPermission.mockImplementation(() => true);
  });

  it("renders an Add Model link pointing to the dedicated page when user has model:add", async () => {
    renderModels();
    const link = await screen.findByRole("link", { name: /add model/i });
    expect(link).toHaveAttribute("href", "/dashboard/models/new");
  });

  it("hides the Add Model link when user lacks model:add permission", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm !== "model:add");
    renderModels();
    await waitFor(() => {
      expect(screen.getByText("Models")).toBeInTheDocument();
    });
    expect(screen.queryByRole("link", { name: /add model/i })).not.toBeInTheDocument();
  });
});

describe("Models page — Delete button", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listModels).mockResolvedValue(MOCK_CACHED_MODELS);
    vi.mocked(searchHFModels).mockResolvedValue([]);
    vi.mocked(deleteModel).mockResolvedValue(undefined);
    mockHasPermission.mockImplementation(() => true);
    // Suppress window.confirm
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("calls deleteModel with the model id when Delete is confirmed", async () => {
    const user = userEvent.setup();
    renderModels();

    await waitFor(() => {
      expect(screen.getByText("meta-llama/Llama-3-8B")).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByRole("button", { name: /delete/i });
    await user.click(deleteButtons[0]);

    await waitFor(() => {
      expect(deleteModel).toHaveBeenCalledWith("m1");
    });
  });

  it("does not call deleteModel when confirm is cancelled", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    renderModels();

    await waitFor(() => {
      expect(screen.getByText("meta-llama/Llama-3-8B")).toBeInTheDocument();
    });

    const deleteButtons = screen.getAllByRole("button", { name: /delete/i });
    await user.click(deleteButtons[0]);

    expect(deleteModel).not.toHaveBeenCalled();
  });

  it("hides Delete buttons when user lacks model:delete permission", async () => {
    mockHasPermission.mockImplementation((perm: string) => perm !== "model:delete");
    renderModels();

    await waitFor(() => {
      expect(screen.getByText("meta-llama/Llama-3-8B")).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: /delete/i })).not.toBeInTheDocument();
  });
});

describe("Models page — formatBytes helper (via rendered output)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockHasPermission.mockImplementation(() => true);
    vi.mocked(addModel).mockResolvedValue({ id: "new-id" });
    vi.mocked(deleteModel).mockResolvedValue(undefined);
  });

  it("renders bytes_done / bytes_total in human-readable format", async () => {
    const model: CachedModel = {
      id: "x1",
      source: "hf",
      model_id: "test/model",
      revision: "",
      status: "downloading",
      bytes_total: 1_073_741_824, // 1 GB
      bytes_done: 536_870_912,    // 512 MB
    };
    vi.mocked(listModels).mockResolvedValue([model]);
    vi.mocked(searchHFModels).mockResolvedValue([]);
    renderModels();

    await waitFor(() => {
      const sizeSpans = screen.getAllByText((_, el) => {
        if (!el || el.tagName !== "SPAN") return false;
        const t = el.textContent ?? "";
        return t.includes("512.0 MB") && t.includes("1.0 GB");
      });
      expect(sizeSpans.length).toBeGreaterThan(0);
    });
  });

  it("does not show a progress bar when bytes_total is 0 (guard divide-by-zero)", async () => {
    const model: CachedModel = {
      id: "x2",
      source: "hf",
      model_id: "test/zero-size",
      revision: "",
      status: "downloading",
      bytes_total: 0,
      bytes_done: 0,
    };
    vi.mocked(listModels).mockResolvedValue([model]);
    vi.mocked(searchHFModels).mockResolvedValue([]);
    renderModels();

    await waitFor(() => {
      expect(screen.getByText("test/zero-size")).toBeInTheDocument();
    });
    // Progress bar should NOT be rendered when bytes_total=0
    expect(screen.queryByText(/^0%$/)).not.toBeInTheDocument();
  });
});
