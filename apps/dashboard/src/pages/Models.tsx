import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Search, Trash2, Plus, Database, AlertCircle, X } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useAuth } from "@/context/AuthContext";
import {
  listModels,
  addModel,
  deleteModel,
  type CachedModel,
} from "@/services/modelService";
import {
  searchHFModels,
  type HFModel,
} from "@/services/huggingfaceService";
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

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const idx = Math.min(i, units.length - 1);
  return `${(bytes / Math.pow(1024, idx)).toFixed(1)} ${units[idx]}`;
}

function getStatusStyles(status: string) {
  if (status === "cached") {
    return "border-green-200 bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800";
  }
  if (status === "error") {
    return "border-red-200 bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800";
  }
  if (status === "downloading") {
    return "border-blue-200 bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400 dark:border-blue-800";
  }
  // pending
  return "border-yellow-200 bg-yellow-50 text-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800";
}

function getStatusDot(status: string) {
  if (status === "cached") return "bg-green-500";
  if (status === "error") return "bg-red-500";
  if (status === "downloading") return "bg-blue-500 animate-pulse";
  return "bg-yellow-500";
}

function hasActiveDownload(models: CachedModel[]): boolean {
  return models.some(
    (m) => m.status === "downloading" || m.status === "pending"
  );
}

export default function Models() {
  const queryClient = useQueryClient();
  const { hasPermission } = useAuth();
  const canAdd = hasPermission("model:add");
  const canDelete = hasPermission("model:delete");

  // All hooks unconditional at top — no early return before hooks
  const [showSearch, setShowSearch] = useState(false);
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
    data: models = [],
    isLoading: modelsLoading,
  } = useQuery<CachedModel[]>({
    queryKey: ["models"],
    queryFn: listModels,
    refetchInterval: (query) => {
      const data = query.state.data ?? [];
      return hasActiveDownload(data) ? 3000 : false;
    },
  });

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

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteModel(id),
    onSuccess: () => {
      toast.success("Model deleted");
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (err: AxiosError<ApiErrorResponse>) => {
      toast.error(err.response?.data?.detail || "Failed to delete model");
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
    <div className="space-y-5 font-sans text-foreground dark:text-cream">
      {/* Header */}
      <div className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Models</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Search Hugging Face models and manage your cached model library.
            </p>
          </div>
          {canAdd && (
            <button
              type="button"
              onClick={() => setShowSearch((v) => !v)}
              className="h-9 px-4 inline-flex items-center gap-2 bg-ember-600 text-white rounded-md text-sm font-medium hover:bg-ember-700 transition-colors shadow-sm shrink-0"
            >
              <Plus className="w-4 h-4" /> Add Model
            </button>
          )}
        </div>
      </div>

      {/* Add Model panel — revealed by the "Add Model" button */}
      {canAdd && showSearch && (
        <div className="rounded-xl border bg-card p-5 shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold">Add Model</h2>
            <button
              type="button"
              onClick={() => setShowSearch(false)}
              aria-label="Close search"
              className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

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
      )}

      {/* Cached Models List */}
      <div className="rounded-xl border bg-card overflow-hidden shadow-sm">
        <div className="px-5 py-4 border-b border-border/70 flex items-center justify-between">
          <h2 className="text-base font-semibold">Cached Models</h2>
          <span className="text-xs text-muted-foreground">{models.length} model{models.length !== 1 ? "s" : ""}</span>
        </div>

        {modelsLoading ? (
          <div className="p-8">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-14 w-full bg-muted dark:bg-card animate-pulse rounded mb-2" />
            ))}
          </div>
        ) : models.length === 0 ? (
          <div className="px-4 py-16 text-center text-muted-foreground">
            <Database className="h-8 w-8 mx-auto mb-2 opacity-25" />
            <p className="text-sm">No cached models yet.</p>
            {canAdd && (
              <p className="text-xs mt-1">Click "Add Model" to search Hugging Face.</p>
            )}
          </div>
        ) : (
          <div className="divide-y">
            {models.map((model) => (
              <ModelRow
                key={model.id}
                model={model}
                canDelete={canDelete}
                isDeleting={deleteMutation.isPending}
                onDelete={(id) => {
                  if (confirm("Permanently delete this cached model?")) {
                    deleteMutation.mutate(id);
                  }
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ModelRow({
  model,
  canDelete,
  isDeleting,
  onDelete,
}: {
  model: CachedModel;
  canDelete: boolean;
  isDeleting: boolean;
  onDelete: (id: string) => void;
}) {
  const progressPct =
    model.bytes_total > 0
      ? Math.min(100, Math.round((model.bytes_done / model.bytes_total) * 100))
      : 0;

  return (
    <div className="px-5 py-4 bg-background hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-sm truncate">{model.model_id}</span>
            <span
              className={cn(
                "inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium border",
                getStatusStyles(model.status)
              )}
            >
              <span className={cn("h-1.5 w-1.5 rounded-full", getStatusDot(model.status))} />
              {model.status}
            </span>
          </div>

          <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
            <span>
              <span className="uppercase tracking-wide">source:</span> {model.source}
            </span>
            {model.engine_hint && (
              <span>
                <span className="uppercase tracking-wide">engine:</span> {model.engine_hint}
              </span>
            )}
            {model.revision && (
              <span className="font-mono">{model.revision.slice(0, 8)}</span>
            )}
            {model.status === "cached" && (model.bytes_done > 0 || model.bytes_total > 0) ? (
              <span className="font-medium text-foreground/80">
                {formatBytes(model.bytes_done || model.bytes_total)} on disk
              </span>
            ) : model.bytes_total > 0 ? (
              <span>
                {formatBytes(model.bytes_done)} / {formatBytes(model.bytes_total)}
              </span>
            ) : null}
          </div>

          {(model.status === "downloading" || model.status === "pending") && model.bytes_total > 0 && (
            <div className="mt-1 flex items-center gap-2 max-w-xs">
              <div className="h-1.5 flex-1 rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full bg-blue-500 transition-all duration-500"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
              <span className="text-[11px] text-muted-foreground tabular-nums shrink-0">
                {progressPct}%
              </span>
            </div>
          )}

          {model.status === "error" && model.error && (
            <div className="flex items-start gap-1 text-[11px] text-red-600 dark:text-red-400 mt-1">
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
              <span className="line-clamp-2">{model.error}</span>
            </div>
          )}
        </div>

        {canDelete && (
          <button
            type="button"
            disabled={isDeleting}
            onClick={() => onDelete(model.id)}
            className="inline-flex items-center gap-1.5 rounded-md border border-red-500/20 bg-red-500/10 px-2.5 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-500/20 font-medium disabled:opacity-50 transition-colors shrink-0"
          >
            <Trash2 className="w-3.5 h-3.5" />
            Delete
          </button>
        )}
      </div>
    </div>
  );
}
