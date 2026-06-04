import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Search, Plus, ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { addModel } from "@/services/modelService";
import { searchHFModels, type HFModel } from "@/services/huggingfaceService";
import type { AxiosError } from "axios";

type ApiErrorResponse = { detail?: string };
type AddSource = "hf" | "ollama";

// Ollama has no public JSON search API (unlike HF), so we surface a curated
// list of popular models and filter it as the user types. The free-form input
// still lets you add any exact `name:tag` reference (e.g. a specific quant).
const OLLAMA_POPULAR = [
  "llama3.3", "llama3.2", "llama3.1", "llama3", "qwen3", "qwen2.5",
  "qwen2.5-coder", "gemma3", "gemma2", "phi4", "phi3", "mistral",
  "mistral-nemo", "mixtral", "deepseek-r1", "codellama", "llava",
  "nomic-embed-text", "snowflake-arctic-embed", "tinyllama", "smollm2",
];

export default function NewModel() {
  const queryClient = useQueryClient();

  const [addSource, setAddSource] = useState<AddSource>("hf");
  const [hfQuery, setHfQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [searchTimer, setSearchTimer] = useState<ReturnType<typeof setTimeout> | null>(null);
  const [ollamaInput, setOllamaInput] = useState("");

  const handleSearchChange = (value: string) => {
    setHfQuery(value);
    if (searchTimer) clearTimeout(searchTimer);
    const timer = setTimeout(() => setDebouncedQuery(value.trim()), 400);
    setSearchTimer(timer);
  };

  const {
    data: hfResults = [],
    isLoading: hfLoading,
    isFetching: hfFetching,
  } = useQuery<HFModel[]>({
    queryKey: ["hf-search", debouncedQuery],
    queryFn: () =>
      debouncedQuery
        ? searchHFModels({ search: debouncedQuery, limit: 20 })
        : Promise.resolve([]),
    enabled: debouncedQuery.length > 0,
  });

  const addMutation = useMutation({
    mutationFn: (modelId: string) =>
      addModel({ source: "hf", model_id: modelId, engine: "vllm" }),
    onSuccess: () => {
      toast.success("Model queued for download");
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (err: AxiosError<ApiErrorResponse>) => {
      toast.error(err.response?.data?.detail || "Failed to add model");
    },
  });

  const addOllamaMutation = useMutation({
    mutationFn: ({ name, tag }: { name: string; tag: string }) =>
      addModel({ source: "ollama", model_id: name, revision: tag, engine: "ollama" }),
    onSuccess: () => {
      toast.success("Ollama model queued for download");
      queryClient.invalidateQueries({ queryKey: ["models"] });
      setOllamaInput("");
    },
    onError: (err: AxiosError<ApiErrorResponse>) => {
      toast.error(err.response?.data?.detail || "Failed to add Ollama model");
    },
  });

  const addOllamaByRef = (ref: string) => {
    const trimmed = ref.trim();
    if (!trimmed) return;
    const colonIdx = trimmed.lastIndexOf(":");
    const name = colonIdx > 0 ? trimmed.slice(0, colonIdx) : trimmed;
    const tag = colonIdx > 0 ? trimmed.slice(colonIdx + 1) : "latest";
    addOllamaMutation.mutate({ name, tag });
  };

  const handleAddOllama = () => addOllamaByRef(ollamaInput);

  // Curated suggestions filtered by the typed model name (the part before any
  // ":tag"). Shown below the input like the HF search results.
  const ollamaQuery = ollamaInput.split(":")[0].trim().toLowerCase();
  const ollamaSuggestions = OLLAMA_POPULAR.filter(
    (m) => !ollamaQuery || m.toLowerCase().includes(ollamaQuery)
  );

  return (
    <div className="max-w-4xl mx-auto space-y-8 animate-in fade-in duration-500 font-sans text-foreground dark:text-cream">
      {/* Header — mirrors the NewPool page header + back link */}
      <div>
        <Link
          to="/dashboard/models"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors mb-3"
        >
          <ArrowLeft className="w-4 h-4" /> Back to Models
        </Link>
        <h2 className="text-3xl font-bold tracking-tight">Add Model</h2>
        <p className="text-muted-foreground mt-2">
          Search Hugging Face or Ollama and queue a model for download into the
          shared control-plane cache. Added models appear on the Models page with
          live download progress.
        </p>
      </div>

      <div className="rounded-xl border bg-card p-6 shadow-sm space-y-5">
        {/* Source selector: Hugging Face | Ollama */}
        <div className="inline-flex rounded-md border overflow-hidden text-sm font-medium">
          <button
            type="button"
            onClick={() => setAddSource("hf")}
            className={cn(
              "px-4 py-1.5 transition-colors",
              addSource === "hf"
                ? "bg-ember-600 text-white"
                : "bg-background text-muted-foreground hover:bg-muted"
            )}
          >
            Hugging Face
          </button>
          <button
            type="button"
            onClick={() => setAddSource("ollama")}
            className={cn(
              "px-4 py-1.5 transition-colors border-l",
              addSource === "ollama"
                ? "bg-ember-600 text-white"
                : "bg-background text-muted-foreground hover:bg-muted"
            )}
          >
            Ollama
          </button>
        </div>

        {/* Hugging Face search (default) */}
        {addSource === "hf" && (
          <>
            <div className="relative w-full max-w-lg">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <input
                name="hf-search"
                autoComplete="off"
                placeholder="Search models on Hugging Face…"
                className="h-9 w-full rounded-md border dark:border-border bg-background pl-9 pr-4 text-sm outline-none focus:ring-1 focus:ring-ember-500 shadow-sm"
                value={hfQuery}
                onChange={(e) => handleSearchChange(e.target.value)}
              />
            </div>

            {(hfLoading || hfFetching) && debouncedQuery && (
              <div className="text-sm text-muted-foreground">Searching…</div>
            )}

            {!hfLoading && !hfFetching && debouncedQuery && hfResults.length === 0 && (
              <div className="text-sm text-muted-foreground">No results found for "{debouncedQuery}".</div>
            )}

            {hfResults.length > 0 && (
              <div className="divide-y rounded-lg border overflow-hidden">
                {hfResults.map((model) => (
                  <div
                    key={model.id}
                    className="flex items-center justify-between gap-4 px-4 py-3 bg-background hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-sm truncate">{model.id}</div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {model.pipeline_tag && (
                          <span className="mr-2">{model.pipeline_tag}</span>
                        )}
                        {model.downloads != null && (
                          <span>{model.downloads.toLocaleString()} downloads</span>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      disabled={addMutation.isPending}
                      onClick={() => addMutation.mutate(model.id)}
                      className="inline-flex items-center gap-1.5 rounded-md border border-ember-500/20 bg-ember-500/10 px-2.5 py-1.5 text-xs text-ember-600 dark:text-ember-400 hover:bg-ember-500/20 font-medium disabled:opacity-50 transition-colors shrink-0"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      Add
                    </button>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {/* Ollama model input + live suggestions (like HF search) */}
        {addSource === "ollama" && (
          <div className="space-y-4">
            <div className="relative w-full max-w-lg">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <input
                name="ollama-model"
                autoComplete="off"
                placeholder="Search Ollama models, or type a name:tag (e.g. gemma3:4b)…"
                className="h-9 w-full rounded-md border dark:border-border bg-background pl-9 pr-4 text-sm outline-none focus:ring-1 focus:ring-ember-500 shadow-sm"
                value={ollamaInput}
                onChange={(e) => setOllamaInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAddOllama();
                }}
              />
            </div>

            {(ollamaInput.trim() || ollamaSuggestions.length > 0) && (
              <div className="divide-y rounded-lg border overflow-hidden">
                {/* Add exactly what was typed (covers specific tags). */}
                {ollamaInput.trim() && (
                  <div className="flex items-center justify-between gap-4 px-4 py-3 bg-background hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-sm truncate">{ollamaInput.trim()}</div>
                      <div className="text-xs text-muted-foreground mt-0.5">
                        {ollamaInput.includes(":") ? "exact reference" : "tag: latest"}
                      </div>
                    </div>
                    <button
                      type="button"
                      disabled={addOllamaMutation.isPending}
                      onClick={() => addOllamaByRef(ollamaInput)}
                      className="inline-flex items-center gap-1.5 rounded-md border border-ember-500/20 bg-ember-500/10 px-2.5 py-1.5 text-xs text-ember-600 dark:text-ember-400 hover:bg-ember-500/20 font-medium disabled:opacity-50 transition-colors shrink-0"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      Add
                    </button>
                  </div>
                )}
                {/* Curated popular models, filtered by the typed query. */}
                {ollamaSuggestions
                  .filter((m) => m !== ollamaInput.trim())
                  .map((m) => (
                    <div
                      key={m}
                      className="flex items-center justify-between gap-4 px-4 py-3 bg-background hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="font-medium text-sm truncate">{m}</div>
                        <div className="text-xs text-muted-foreground mt-0.5">popular · tag: latest</div>
                      </div>
                      <button
                        type="button"
                        disabled={addOllamaMutation.isPending}
                        onClick={() => addOllamaByRef(m)}
                        className="inline-flex items-center gap-1.5 rounded-md border border-ember-500/20 bg-ember-500/10 px-2.5 py-1.5 text-xs text-ember-600 dark:text-ember-400 hover:bg-ember-500/20 font-medium disabled:opacity-50 transition-colors shrink-0"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        Add
                      </button>
                    </div>
                  ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
