import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi, beforeEach } from "vitest";

// ── Mock services ──────────────────────────────────────────────────────────────

vi.mock("@/services/modelService", () => ({
  addModel: vi.fn(),
}));

vi.mock("@/services/huggingfaceService", () => ({
  searchHFModels: vi.fn(),
}));

// ── Import after mocks ─────────────────────────────────────────────────────────

import NewModel from "@/pages/NewModel";
import { addModel } from "@/services/modelService";
import { searchHFModels } from "@/services/huggingfaceService";
import type { HFModel } from "@/services/huggingfaceService";

// ── Test helpers ───────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: Infinity },
    },
  });
}

function renderNewModel() {
  const qc = makeQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <NewModel />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const MOCK_HF_RESULTS: HFModel[] = [
  {
    id: "facebook/opt-125m",
    modelId: "facebook/opt-125m",
    author: "facebook",
    lastModified: "2023-01-01",
    tags: ["text-generation"],
    pipeline_tag: "text-generation",
    downloads: 100000,
    likes: 500,
    library_name: "transformers",
  },
  {
    id: "gpt2",
    modelId: "gpt2",
    author: "openai",
    lastModified: "2023-01-01",
    tags: ["text-generation"],
    pipeline_tag: "text-generation",
    downloads: 5000000,
    likes: 10000,
    library_name: "transformers",
  },
];

// ── Tests ──────────────────────────────────────────────────────────────────────

describe("NewModel page — layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(searchHFModels).mockResolvedValue([]);
    vi.mocked(addModel).mockResolvedValue({ id: "new-id" });
  });

  it("renders as a dedicated page with a Back to Models link", () => {
    renderNewModel();
    const back = screen.getByRole("link", { name: /back to models/i });
    expect(back).toHaveAttribute("href", "/dashboard/models");
  });

  it("shows the HF search by default (source selector defaults to Hugging Face)", () => {
    renderNewModel();
    expect(screen.getByPlaceholderText(/Search models on Hugging Face/i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/ollama model/i)).not.toBeInTheDocument();
  });

  it("switches from HF to Ollama view and back", async () => {
    const user = userEvent.setup();
    renderNewModel();

    expect(screen.getByPlaceholderText(/Search models on Hugging Face/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /ollama/i }));
    expect(screen.queryByPlaceholderText(/Search models on Hugging Face/i)).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText(/ollama model/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /hugging face/i }));
    expect(screen.getByPlaceholderText(/Search models on Hugging Face/i)).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/ollama model/i)).not.toBeInTheDocument();
  });
});

describe("NewModel page — HF search", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(searchHFModels).mockResolvedValue(MOCK_HF_RESULTS);
    vi.mocked(addModel).mockResolvedValue({ id: "new-id" });
  });

  it("calls addModel with the model id when Add is clicked", async () => {
    const user = userEvent.setup();
    renderNewModel();

    const input = screen.getByPlaceholderText(/Search models on Hugging Face/i);
    await user.type(input, "opt");

    await waitFor(() => {
      expect(screen.getByText("facebook/opt-125m")).toBeInTheDocument();
    }, { timeout: 2000 });

    // Structure: outer flex row div > [info div, button]
    const textEl = screen.getByText("facebook/opt-125m");
    const flexRow = textEl.closest("div")?.parentElement?.parentElement;
    if (!flexRow) throw new Error("Could not find opt-125m row");
    const addButton = within(flexRow).getByRole("button", { name: /add/i });
    await user.click(addButton);

    await waitFor(() => {
      expect(addModel).toHaveBeenCalledWith({
        source: "hf",
        model_id: "facebook/opt-125m",
        engine: "vllm",
      });
    });
  });
});

describe("NewModel page — Ollama Add flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(searchHFModels).mockResolvedValue([]);
    vi.mocked(addModel).mockResolvedValue({ id: "new-id" });
  });

  it("calls addModel with ollama params when user types name:tag and clicks Add", async () => {
    const user = userEvent.setup();
    renderNewModel();

    await user.click(screen.getByRole("button", { name: /ollama/i }));

    const ollamaInput = screen.getByPlaceholderText(/ollama model/i);
    await user.type(ollamaInput, "gemma3:4b");

    const addButtons = screen.getAllByRole("button", { name: /^add$/i });
    await user.click(addButtons[0]);

    await waitFor(() => {
      expect(addModel).toHaveBeenCalledWith({
        source: "ollama",
        model_id: "gemma3",
        revision: "4b",
        engine: "ollama",
      });
    });
  });

  it("defaults to 'latest' tag when no colon in ollama input", async () => {
    const user = userEvent.setup();
    renderNewModel();

    await user.click(screen.getByRole("button", { name: /ollama/i }));

    const ollamaInput = screen.getByPlaceholderText(/ollama model/i);
    await user.type(ollamaInput, "llama3");

    const addButtons = screen.getAllByRole("button", { name: /^add$/i });
    await user.click(addButtons[0]);

    await waitFor(() => {
      expect(addModel).toHaveBeenCalledWith({
        source: "ollama",
        model_id: "llama3",
        revision: "latest",
        engine: "ollama",
      });
    });
  });

  it("clears ollama input after successful add", async () => {
    const user = userEvent.setup();
    renderNewModel();

    await user.click(screen.getByRole("button", { name: /ollama/i }));

    const ollamaInput = screen.getByPlaceholderText(/ollama model/i);
    await user.type(ollamaInput, "gemma3:4b");

    const addButtons = screen.getAllByRole("button", { name: /^add$/i });
    await user.click(addButtons[0]);

    await waitFor(() => {
      expect(addModel).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(ollamaInput).toHaveValue("");
    });
  });
});
