import { useState, useEffect, useCallback, useReducer, useMemo } from "react"
import {
  Cpu, Server, Check, Zap, Globe, Layers, Terminal, Box, Rocket, Brain, Wrench,
  Database, Image, Eye, Volume2, Search, X, ChevronDown, Star, Download, Loader2,
  MessageSquare, ExternalLink
} from "lucide-react"
import { computeApi } from "@/lib/api"
import { toast } from "sonner"
import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query"
import { useNavigate, Link } from "react-router-dom"
import { cn } from "@/lib/utils"
import { useAuth } from "@/context/AuthContext"
import {
  searchHFModels,
  getPopularModels,
  EMBEDDING_MODELS,
  MODEL_TYPES,
  inferModelType,
  formatDownloads,
  type HFModel,
  type ModelTypeKey
} from "@/services/huggingfaceService"

// --- Constants ---

const deploymentTypes = [
  {
    id: "inference",
    name: "Inference",
    desc: "Deploy LLMs for real-time text generation.",
    icon: MessageSquare,
    modelType: "inference" as ModelTypeKey,
    active: true
  },
  {
    id: "embedding",
    name: "Embeddings",
    desc: "Deploy embedding models for semantic search and RAG.",
    icon: Database,
    modelType: "embedding" as ModelTypeKey,
    active: true
  },
  {
    id: "training",
    name: "Training",
    desc: "Fine-tune models on your custom datasets.",
    icon: Brain,
    modelType: "training" as ModelTypeKey,
    active: true
  },
  {
    id: "image",
    name: "Image Generation",
    desc: "Deploy Stable Diffusion and image generation models.",
    icon: Image,
    modelType: "image_generation" as ModelTypeKey,
    active: false,
    badge: "Soon"
  },
  {
    id: "multimodal",
    name: "Multimodal",
    desc: "Deploy vision-language models.",
    icon: Eye,
    modelType: "multimodal" as ModelTypeKey,
    active: false,
    badge: "Soon"
  },
  {
    id: "audio",
    name: "Audio",
    desc: "Deploy speech recognition and TTS models.",
    icon: Volume2,
    modelType: "audio" as ModelTypeKey,
    active: false,
    badge: "Soon"
  },
]

const computeEngines = [
  {
    id: "vllm",
    name: "vLLM",
    desc: "High-throughput and memory-efficient LLM serving engine.",
    image: "docker.io/vllm/vllm-openai:v0.14.0",
    icon: Cpu,
    types: ["inference", "multimodal"],
    modelTypes: ["inference", "multimodal"]
  },
  {
    id: "ollama",
    name: "Ollama",
    desc: "Run huge models locally with ease.",
    image: "ollama/ollama:latest",
    icon: Terminal,
    types: ["inference"],
    modelTypes: ["inference", "multimodal"]
  },
  {
    id: "infinity",
    name: "Infinity (Embeddings)",
    desc: "High-performance embedding server for sentence-transformers.",
    image: "michaelf34/infinity:latest",
    icon: Database,
    types: ["inference"],
    modelTypes: ["embedding"]
  },
  {
    id: "tei",
    name: "Text Embeddings Inference",
    desc: "Hugging Face's official embedding server.",
    image: "ghcr.io/huggingface/text-embeddings-inference:latest",
    icon: Database,
    types: ["inference"],
    modelTypes: ["embedding"]
  },
  {
    id: "pytorch",
    name: "PyTorch",
    desc: "Standard deep learning container for training.",
    image: "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
    icon: Brain,
    types: ["training"],
    modelTypes: ["training"]
  },
]

const externalProviders = [
  { id: "openai", name: "OpenAI", desc: "GPT + text-embedding models", icon: Globe, defaultEndpoint: "https://api.openai.com", modelTypes: ["inference", "embedding"] as ModelTypeKey[] },
  { id: "anthropic", name: "Anthropic", desc: "Claude chat/completion models", icon: Globe, defaultEndpoint: "https://api.anthropic.com", modelTypes: ["inference"] as ModelTypeKey[] },
  { id: "cohere", name: "Cohere", desc: "Command + embedding models", icon: Globe, defaultEndpoint: "https://api.cohere.ai", modelTypes: ["inference", "embedding"] as ModelTypeKey[] },
  { id: "groq", name: "Groq", desc: "Fast inference via OpenAI-compatible API", icon: Globe, defaultEndpoint: "https://api.groq.com/openai/v1", modelTypes: ["inference"] as ModelTypeKey[] },
  { id: "openrouter", name: "OpenRouter", desc: "Unified API for LLMs and embeddings", icon: Globe, defaultEndpoint: "https://openrouter.ai/api/v1", modelTypes: ["inference", "embedding"] as ModelTypeKey[] },
  { id: "cerebras", name: "Cerebras", desc: "Wafer-scale inference models", icon: Cpu, defaultEndpoint: "https://api.cerebras.ai/v1", modelTypes: ["inference"] as ModelTypeKey[] },
  { id: "custom", name: "Custom OpenAI", desc: "Compatible provider for inference or embeddings", icon: Server, defaultEndpoint: "", modelTypes: ["inference", "embedding"] as ModelTypeKey[] },
]

// --- Types ---

type State = {
  mode: "managed" | "external";
  step: number;
  deploymentType: string;
  modelType: ModelTypeKey;
  instanceName: string;
  selectedEngine: string;
  selectedPool: any;
  userPools: any[];
  selectedHFModel: HFModel | null;
  jobDescription: string;
  modelId: string;
  gitRepo: string;
  trainingScript: string;
  datasetUrl: string;
  baseModel: string;
  embeddingDimensions: string;
  maxSequenceLength: string;
  batchSize: string;
  maxModelLen: string;
  gpuUtil: string;
  hfToken: string;
  vllmImage: string;
  selectedProvider: string;
  customProviderName: string;
  externalModelName: string;
  endpointUrl: string;
  apiKey: string;
};

type Action =
  | { type: 'SET_MODE'; payload: "managed" | "external" }
  | { type: 'SET_STEP'; payload: number }
  | { type: 'SET_FIELD'; field: keyof State; value: any }
  | { type: 'INIT_POOLS'; payload: any[] }
  | { type: 'SELECT_TYPE'; deploymentType: string; modelType: ModelTypeKey };

// --- Reducer ---

function deploymentReducer(state: State, action: Action): State {
  switch (action.type) {
    case 'SET_MODE':
      return { ...initialState, mode: action.payload, step: 1 };
    case 'SET_STEP':
      return { ...state, step: action.payload };
    case 'SET_FIELD':
      return { ...state, [action.field]: action.value };
    case 'INIT_POOLS':
      return { ...state, userPools: action.payload };
    case 'SELECT_TYPE':
      return {
        ...state,
        deploymentType: action.deploymentType,
        modelType: action.modelType,
        step: 2
      };
    default:
      return state;
  }
}

const initialState: State = {
  mode: "managed",
  step: 1,
  deploymentType: "inference",
  modelType: "inference",
  instanceName: "",
  selectedEngine: "vllm",
  selectedPool: null,
  userPools: [],
  selectedHFModel: null,
  jobDescription: "",
  modelId: "",
  gitRepo: "",
  trainingScript: "python train.py",
  datasetUrl: "",
  baseModel: "",
  embeddingDimensions: "384",
  maxSequenceLength: "512",
  batchSize: "32",
  maxModelLen: "8192",
  gpuUtil: "0.95",
  hfToken: "",
  vllmImage: "docker.io/vllm/vllm-openai:v0.14.0",
  selectedProvider: "",
  customProviderName: "",
  externalModelName: "",
  endpointUrl: "",
  apiKey: "",
};

// --- Components ---

function StepIndicator({ step, current, label }: { step: number; current: number; label: string }) {
  const isActive = step >= current
  return (
    <div className={cn("flex items-center gap-2", isActive && "text-blue-600 dark:text-blue-400")}>
      <div className={cn(
        "w-6 h-6 rounded-full flex items-center justify-center text-xs border transition-all",
        isActive ? "bg-blue-600 text-white border-blue-600 dark:border-blue-500 dark:bg-blue-600" : "border-slate-300 dark:border-zinc-700 bg-white dark:bg-zinc-800"
      )}>
        {current}
      </div>
      {label}
    </div>
  )
}

function HuggingFaceModelBrowser({
  modelType,
  onSelect,
  selectedModelId,
}: {
  modelType: ModelTypeKey
  onSelect: (model: HFModel) => void
  selectedModelId: string
}) {
  const [searchQuery, setSearchQuery] = useState("")
  const [showBrowser, setShowBrowser] = useState(false)

  const { data: popularModels, isLoading: loadingPopular } = useQuery({
    queryKey: ["hf-popular", modelType],
    queryFn: () => getPopularModels(modelType, 10),
    enabled: showBrowser && modelType !== "embedding",
  })

  const { data: searchResults, isLoading: loadingSearch } = useQuery({
    queryKey: ["hf-search", searchQuery, modelType],
    queryFn: () => searchHFModels({
      search: searchQuery,
      pipeline_tag: MODEL_TYPES[modelType]?.pipeline_tags[0],
      limit: 20
    }),
    enabled: showBrowser && searchQuery.length > 2,
  })

  const displayModels = searchQuery.length > 2 ? searchResults : popularModels
  const isLoading = searchQuery.length > 2 ? loadingSearch : loadingPopular

  const embeddingModels = modelType === "embedding" ? EMBEDDING_MODELS : []

  if (!showBrowser) {
    return (
      <button
        onClick={() => setShowBrowser(true)}
        className="w-full px-3 py-2 text-sm border border-dashed border-blue-300 dark:border-blue-700 rounded-md hover:bg-blue-50 dark:hover:bg-blue-900/20 text-blue-600 dark:text-blue-400 flex items-center justify-center gap-2 transition-colors"
      >
        <Search className="w-4 h-4" />
        Browse Hugging Face Models
      </button>
    )
  }

  return (
    <div className="border rounded-lg overflow-hidden bg-white dark:bg-zinc-900">
      <div className="p-3 border-b dark:border-zinc-700 flex items-center gap-2">
        <Search className="w-4 h-4 text-slate-400" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder={`Search ${MODEL_TYPES[modelType]?.label || "models"} on Hugging Face...`}
          className="flex-1 text-sm outline-none bg-transparent"
        />
        <button
          onClick={() => setShowBrowser(false)}
          className="p-1 hover:bg-slate-100 dark:hover:bg-zinc-800 rounded"
        >
          <X className="w-4 h-4 text-slate-400" />
        </button>
      </div>

      <div className="max-h-64 overflow-y-auto">
        {isLoading ? (
          <div className="p-8 text-center text-slate-500">
            <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
            Loading models...
          </div>
        ) : modelType === "embedding" ? (
          <div className="divide-y dark:divide-zinc-700">
            {embeddingModels.map((model) => (
              <button
                key={model.id}
                onClick={() => {
                  onSelect({
                    id: model.id,
                    modelId: model.id,
                    author: model.id.split("/")[0],
                    lastModified: "",
                    tags: ["sentence-transformers"],
                    pipeline_tag: "feature-extraction",
                    downloads: model.downloads,
                    likes: 0,
                    library_name: "sentence-transformers",
                  } as HFModel)
                  setShowBrowser(false)
                }}
                className={cn(
                  "w-full p-3 text-left hover:bg-slate-50 dark:hover:bg-zinc-800 transition-colors flex items-start gap-3",
                  selectedModelId === model.id && "bg-blue-50 dark:bg-blue-900/20 border-l-2 border-blue-500"
                )}
              >
                <Database className="w-5 h-5 text-slate-400 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm truncate">{model.name}</div>
                  <div className="text-xs text-slate-500 mt-0.5">{model.description}</div>
                  <div className="flex items-center gap-3 mt-1.5 text-xs text-slate-400">
                    <span>{model.dimensions}d</span>
                    <span>•</span>
                    <span>Max {model.max_sequence_length} tokens</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        ) : displayModels?.length ? (
          <div className="divide-y dark:divide-zinc-700">
            {displayModels.map((model: HFModel) => (
              <button
                key={model.id}
                onClick={() => {
                  onSelect(model)
                  setShowBrowser(false)
                }}
                className={cn(
                  "w-full p-3 text-left hover:bg-slate-50 dark:hover:bg-zinc-800 transition-colors",
                  selectedModelId === model.id && "bg-blue-50 dark:bg-blue-900/20 border-l-2 border-blue-500"
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm truncate">{model.id}</div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-slate-400">
                      <span className="flex items-center gap-1">
                        <Download className="w-3 h-3" />
                        {formatDownloads(model.downloads || 0)}
                      </span>
                      <span className="flex items-center gap-1">
                        <Star className="w-3 h-3" />
                        {model.likes || 0}
                      </span>
                      {model.pipeline_tag && (
                        <span className="px-1.5 py-0.5 bg-slate-100 dark:bg-zinc-800 rounded text-[10px]">
                          {model.pipeline_tag}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div className="p-8 text-center text-slate-500 text-sm">
            {searchQuery.length > 2
              ? "No models found. Try a different search term."
              : "Type to search for models on Hugging Face"
            }
          </div>
        )}
      </div>
    </div>
  )
}

// --- Main Page Component ---

export default function NewDeployment() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { user, organizations } = useAuth()

  const [state, dispatch] = useReducer(deploymentReducer, initialState);
  const {
    mode, step, deploymentType, modelType, instanceName, selectedEngine,
    selectedPool, userPools, selectedHFModel, jobDescription, modelId,
    gitRepo, trainingScript, datasetUrl, baseModel, batchSize,
    maxSequenceLength, maxModelLen, gpuUtil, hfToken, vllmImage,
    selectedProvider, customProviderName, externalModelName, endpointUrl, apiKey
  } = state;

  const externalModelType = modelType === "embedding" ? "embedding" : "inference"
  const filteredExternalProviders = externalProviders.filter((provider) => provider.modelTypes.includes(externalModelType))

  // --- Effects ---

  useEffect(() => {
    const availableEngines = computeEngines.filter(e => e.modelTypes.includes(modelType))
    if (availableEngines.length > 0) {
      dispatch({ type: 'SET_FIELD', field: 'selectedEngine', value: availableEngines[0].id });
    }
  }, [modelType])

  useEffect(() => {
    if (mode === "managed" && step === 3) {
      const fetchPools = async () => {
        try {
          const targetOrgId = user?.org_id || organizations?.[0]?.id;
          if (!targetOrgId) return;
          const res = await computeApi.get(`/deployment/listPools/${targetOrgId}`)
          if (res.data?.pools) {
            dispatch({ type: 'INIT_POOLS', payload: res.data.pools });
          }
        } catch (e) {
          console.error("Failed to fetch pools", e)
          toast.error("Failed to list compute pools")
        }
      }
      fetchPools()
    }
  }, [mode, step, user, organizations])

  useEffect(() => {
    if (mode === "external" && selectedProvider) {
      const provider = externalProviders.find(p => p.id === selectedProvider)
      if (provider?.defaultEndpoint && !endpointUrl) {
        dispatch({ type: 'SET_FIELD', field: 'endpointUrl', value: provider.defaultEndpoint });
      }
    }
  }, [selectedProvider, mode, endpointUrl])

  useEffect(() => {
    if (mode !== "external") return
    const providerStillValid = externalProviders.some(
      provider => provider.id === selectedProvider && provider.modelTypes.includes(externalModelType)
    )
    if (!providerStillValid) {
      dispatch({ type: 'SET_FIELD', field: 'selectedProvider', value: "" });
      dispatch({ type: 'SET_FIELD', field: 'endpointUrl', value: "" });
    }
  }, [externalModelType, mode, selectedProvider])

  // Split vLLM Logic into dedicated function to avoid multiple setState calls in one effect
  const buildJobSpec = useCallback(() => {
    if (selectedEngine === "vllm" && modelType === "inference") {
      const spec = {
        image: vllmImage,
        cmd: ["--model", modelId || "meta-llama/Meta-Llama-3-8B-Instruct", "--served-model-name", modelId || "meta-llama/Meta-Llama-3-8B-Instruct", "--port", "9000", "--max-model-len", maxModelLen, "--gpu-memory-utilization", gpuUtil, "--max-num-seqs", "256", "--dtype", "auto", "--trust-remote-code"],
        env: hfToken ? { "HF_TOKEN": hfToken } : {},
        expose: [{ "port": 9000, "health_checks": [{ "body": JSON.stringify({ model: modelId || "meta-llama/Meta-Llama-3-8B-Instruct", messages: [{ role: "user", content: "Respond with a single word: Ready" }], stream: false }), "path": "/v1/chat/completions", "type": "http", "method": "POST", "headers": { "Content-Type": "application/json" }, "continuous": false, "expected_status": 200 }] }],
        gpu: true
      }
      return JSON.stringify(spec, null, 4)
    } else if (selectedEngine === "ollama") {
      return JSON.stringify({ image: "ollama/ollama:latest", cmd: ["serve"], expose: [{ port: 11434, type: "http" }], gpu: true }, null, 4)
    } else if (selectedEngine === "infinity") {
      const spec = { image: "michaelf34/infinity:latest", cmd: ["v2", "--model-id", modelId || "sentence-transformers/all-MiniLM-L6-v2", "--port", "7997"], env: { "INFINITY_MODEL_ID": modelId || "sentence-transformers/all-MiniLM-L6-v2", "INFINITY_PORT": "7997", ...(hfToken ? { "HF_TOKEN": hfToken } : {}) }, expose: [{ port: 7997, type: "http", health_checks: [{ path: "/health", type: "http", method: "GET", expected_status: 200 }] }], gpu: false }
      return JSON.stringify(spec, null, 4)
    } else if (selectedEngine === "tei") {
      const spec = { image: "ghcr.io/huggingface/text-embeddings-inference:latest", cmd: ["--model-id", modelId || "sentence-transformers/all-MiniLM-L6-v2", "--port", "8080"], env: hfToken ? { "HF_TOKEN": hfToken } : {}, expose: [{ port: 8080, type: "http", health_checks: [{ path: "/health", type: "http", method: "GET", expected_status: 200 }] }], gpu: false }
      return JSON.stringify(spec, null, 4)
    } else if (selectedEngine === "pytorch") {
      return JSON.stringify({ image: "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime", cmd: ["sleep", "infinity"], gpu: true }, null, 4)
    }
    return ""
  }, [selectedEngine, modelId, maxModelLen, gpuUtil, hfToken, vllmImage, modelType])

  useEffect(() => {
    const spec = buildJobSpec()
    if (spec) {
      dispatch({ type: 'SET_FIELD', field: 'jobDescription', value: spec });
    }
  }, [buildJobSpec])

  // --- Mutations ---

  const createMutation = useMutation({
    mutationFn: async (payload: any) => { await computeApi.post("/deployment/deploy", payload) },
    onSuccess: () => {
      toast.success("Deployment created successfully")
      queryClient.invalidateQueries({ queryKey: ["deployments"] })
      navigate("/dashboard/deployments")
    },
    onError: (err: any) => { toast.error(err.response?.data?.detail || "Failed to create deployment") }
  })

  // --- Handlers ---

  const handleManagedLaunch = async () => {
    if (!instanceName) return toast.error("Please name your deployment")
    if (!selectedPool) return toast.error("Select a compute pool")
    const targetOrgId = user?.org_id || organizations?.[0]?.id;
    if (!targetOrgId) return toast.error("Organization context missing. Please reload.")

    let config = {}
    try { config = JSON.parse(jobDescription) } catch (e) { return toast.error("Invalid Job JSON specification") }

    const payload = {
      model_name: instanceName, model_version: "latest", replicas: 1, gpu_per_replica: modelType === "embedding" ? 0 : 1, workload_type: deploymentType, pool_id: selectedPool.pool_id, engine: selectedEngine, model_type: modelType,
      configuration: deploymentType === "training" ? { workload_type: "training", image: computeEngines.find(e => e.id === selectedEngine)?.image || "pytorch/pytorch:latest", git_repo: gitRepo, training_script: trainingScript, dataset_url: datasetUrl, base_model: baseModel, gpu_count: 1, hf_token: hfToken || undefined } : config,
      owner_id: user?.user_id, org_id: targetOrgId, inference_model: modelId || undefined, job_definition: config
    }
    createMutation.mutate(payload)
  }

  const handleExternalLaunch = async () => {
    if (!instanceName) return toast.error("Please name your deployment")
    if (!selectedProvider) return toast.error("Select a provider")
    if (!externalModelName) return toast.error("Enter a model ID")
    const targetOrgId = user?.org_id || organizations?.[0]?.id;
    if (!targetOrgId) return toast.error("Organization context missing. Please reload.")

    const finalProvider = selectedProvider === 'custom' ? customProviderName : selectedProvider
    const payload = { model_name: instanceName, model_version: "latest", replicas: 1, gpu_per_replica: 0, workload_type: "external", pool_id: "00000000-0000-0000-0000-000000000000", engine: finalProvider, configuration: { provider: finalProvider, model: externalModelName, api_key: apiKey }, endpoint: endpointUrl || undefined, owner_id: user?.user_id, org_id: targetOrgId, model_type: modelType }
    createMutation.mutate(payload)
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8 animate-in fade-in duration-500 font-sans text-slate-900 dark:text-zinc-50">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">New Deployment</h2>
        <p className="text-muted-foreground mt-2">Deploy your models on managed pools or connect to external AI providers.</p>
      </div>

      <div className="flex justify-center">
        <div className="bg-slate-100 dark:bg-zinc-900 p-1 rounded-lg inline-flex shadow-inner">
          <button
            onClick={() => { dispatch({ type: 'SET_MODE', payload: "managed" }); }}
            className={cn("px-6 py-2.5 rounded-md text-sm font-medium transition-all flex items-center gap-2", mode === "managed" ? "bg-white dark:bg-zinc-800 shadow-sm text-blue-600 dark:text-blue-400 ring-1 ring-black/5 dark:ring-white/5" : "text-slate-500 hover:text-slate-900 dark:text-zinc-400 dark:hover:text-zinc-200")}
          >
            <Layers className="w-4 h-4" /> Deploy on Compute
          </button>
          <button
            onClick={() => { dispatch({ type: 'SET_MODE', payload: "external" }); }}
            className={cn("px-6 py-2.5 rounded-md text-sm font-medium transition-all flex items-center gap-2", mode === "external" ? "bg-white dark:bg-zinc-800 shadow-sm text-blue-600 dark:text-blue-400 ring-1 ring-black/5 dark:ring-white/5" : "text-slate-500 hover:text-slate-900 dark:text-zinc-400 dark:hover:text-zinc-200")}
          >
            <Globe className="w-4 h-4" /> External Provider
          </button>
        </div>
      </div>

      {mode === "managed" ? (
        <ManagedFlow
          state={state}
          dispatch={dispatch}
          onLaunch={handleManagedLaunch}
          isPending={createMutation.isPending}
        />
      ) : (
        <ExternalFlow
          state={state}
          dispatch={dispatch}
          onLaunch={handleExternalLaunch}
          isPending={createMutation.isPending}
          filteredProviders={filteredExternalProviders}
          externalModelType={externalModelType}
        />
      )}
    </div>
  )
}

// --- Sub-Components ---

function ManagedFlow({ state, dispatch, onLaunch, isPending }: { state: State; dispatch: React.Dispatch<Action>; onLaunch: () => void; isPending: boolean }) {
  const { step, deploymentType, modelType, instanceName, selectedEngine, selectedPool, userPools, selectedHFModel, jobDescription, modelId, gitRepo, trainingScript, datasetUrl, baseModel, batchSize, maxSequenceLength, maxModelLen, gpuUtil, hfToken, vllmImage } = state;

  return (
    <>
      <div className="flex items-center gap-4 text-sm font-medium text-muted-foreground border-b dark:border-zinc-800 pb-4">
        <StepIndicator step={step} current={1} label="Type" />
        <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
        <StepIndicator step={step} current={2} label="Engine" />
        <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
        <StepIndicator step={step} current={3} label="Pool" />
        <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
        <StepIndicator step={step} current={4} label="Config" />
      </div>

      {step === 1 && <TypeSelection selectedId={deploymentType} onSelect={(id, mt) => dispatch({ type: 'SELECT_TYPE', deploymentType: id, modelType: mt })} />}
      {step === 2 && <EngineSelection modelType={modelType} selectedEngine={selectedEngine} dispatch={dispatch} setStep={(s) => dispatch({ type: 'SET_STEP', payload: s })} />}
      {step === 3 && <PoolSelection userPools={userPools} selectedPool={selectedPool} dispatch={dispatch} setStep={(s) => dispatch({ type: 'SET_STEP', payload: s })} />}
      {step === 4 && <ManagedConfig state={state} dispatch={dispatch} onLaunch={onLaunch} isPending={isPending} />}
    </>
  )
}

function TypeSelection({ selectedId, onSelect }: { selectedId: string; onSelect: (id: string, mt: ModelTypeKey) => void }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {deploymentTypes.map(type => (
        <div
          key={type.id}
          role="button"
          tabIndex={type.active ? 0 : -1}
          onClick={() => type.active && onSelect(type.id, type.modelType)}
          className={cn("p-5 rounded-xl border relative transition-all outline-none", type.active ? "cursor-pointer bg-white dark:bg-zinc-900 dark:border-zinc-800 hover:border-blue-300 dark:hover:border-blue-700 hover:shadow-sm focus:ring-2 focus:ring-blue-500/40" : "opacity-50 cursor-not-allowed bg-slate-50 dark:bg-zinc-900/50 dark:border-zinc-800", selectedId === type.id && type.active ? "border-blue-600 dark:border-blue-500 ring-1 ring-blue-600 dark:ring-blue-500 shadow-md" : "")}
        >
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              {type.icon && <type.icon className={cn("w-5 h-5", type.active ? "text-slate-700 dark:text-zinc-200" : "text-slate-500")} />}
              <h3 className="font-bold">{type.name}</h3>
            </div>
            {type.badge && <span className="text-[10px] font-bold px-2 py-0.5 bg-slate-200 dark:bg-zinc-800 text-slate-600 dark:text-zinc-400 rounded-full uppercase tracking-wide">{type.badge}</span>}
          </div>
          <p className="text-sm text-slate-500 dark:text-zinc-400 leading-relaxed">{type.desc}</p>
        </div>
      ))}
    </div>
  );
}

function EngineSelection({ modelType, selectedEngine, dispatch, setStep }: { modelType: ModelTypeKey; selectedEngine: string; dispatch: React.Dispatch<Action>; setStep: (s: number) => void }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div className="col-span-full">
        <button type="button" onClick={() => setStep(1)} className="text-sm text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200 font-medium mb-4 flex items-center gap-1">← Back to Type</button>
      </div>
      {computeEngines.filter(e => e.modelTypes.includes(modelType)).map(e => (
        <div key={e.id} role="button" onClick={() => dispatch({ type: 'SET_FIELD', field: 'selectedEngine', value: e.id })} className={cn("cursor-pointer p-6 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 relative transition-all outline-none focus:ring-2 focus:ring-blue-500/40", selectedEngine === e.id ? "border-blue-600 dark:border-blue-500 ring-1 ring-blue-600 dark:ring-blue-500 shadow-md" : "hover:border-blue-300 dark:hover:border-blue-700 hover:shadow-sm")}>
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              {e.icon && <e.icon className="w-5 h-5 text-slate-700 dark:text-zinc-200" />}
              <h3 className="font-bold text-lg">{e.name}</h3>
            </div>
            {selectedEngine === e.id && <Check className="w-5 h-5 text-blue-600 dark:text-blue-500" />}
          </div>
          <p className="text-sm text-slate-500 dark:text-zinc-400 leading-relaxed">{e.desc}</p>
        </div>
      ))}
      <div className="col-span-full flex justify-end pt-4"><button type="button" onClick={() => setStep(3)} className="px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors font-medium">Continue</button></div>
    </div>
  );
}

function PoolSelection({ userPools, selectedPool, dispatch, setStep }: { userPools: any[]; selectedPool: any; dispatch: React.Dispatch<Action>; setStep: (s: number) => void }) {
  return (
    <div className="space-y-6">
      {userPools.length === 0 ? (
        <div className="text-center py-12 bg-slate-50 dark:bg-zinc-900/50 rounded-xl border border-dashed dark:border-zinc-800 flex flex-col items-center">
          <Server className="w-12 h-12 text-slate-300 dark:text-zinc-600 mb-4" />
          <h3 className="text-lg font-medium text-slate-900 dark:text-zinc-100">No Compute Pools Found</h3>
          <p className="text-slate-500 dark:text-zinc-400 mt-1 mb-6 max-w-sm">You need active compute resources to deploy this model.</p>
          <Link to="/dashboard/compute/pools/new" className="px-4 py-2 bg-white dark:bg-zinc-900 border border-slate-300 dark:border-zinc-700 rounded-md text-sm font-medium text-slate-700 dark:text-zinc-300 hover:bg-slate-50 dark:hover:bg-zinc-800 shadow-sm flex items-center gap-2"><Zap className="w-4 h-4 text-amber-500" /> Create New Pool</Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {userPools.map(pool => (
            <div key={pool.pool_id} role="button" onClick={() => dispatch({ type: 'SET_FIELD', field: 'selectedPool', value: pool })} className={cn("cursor-pointer p-5 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 relative transition-all outline-none focus:ring-2 focus:ring-blue-500/40", selectedPool?.pool_id === pool.pool_id ? "border-blue-600 dark:border-blue-500 ring-1 ring-blue-600 dark:ring-blue-500 shadow-md" : "hover:border-blue-300 dark:hover:border-blue-700")}>
              <div className="flex items-start justify-between">
                <div><div className="font-bold text-lg">{pool.pool_name}</div><div className="text-sm text-slate-500 dark:text-zinc-400 font-mono mt-1">{pool.provider}</div></div>
                <div className={cn("px-2 py-0.5 rounded text-xs font-medium border", pool.is_active ? "bg-green-50 text-green-700 border-green-200 dark:bg-green-900/20 dark:text-green-400 dark:border-green-900/50" : "bg-slate-50 text-slate-500 border-slate-200 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700")}>{pool.is_active ? "Active" : "Inactive"}</div>
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="flex justify-between pt-6 border-t dark:border-zinc-800"><button type="button" onClick={() => setStep(2)} className="text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200 font-medium">Back</button><button type="button" onClick={() => selectedPool && setStep(4)} disabled={!selectedPool} className="px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 transition-colors font-medium">Continue</button></div>
    </div>
  );
}

function ManagedConfig({ state, dispatch, onLaunch, isPending }: { state: State; dispatch: React.Dispatch<Action>; onLaunch: () => void; isPending: boolean }) {
  const { deploymentType, modelType, instanceName, selectedEngine, selectedHFModel, modelId, vllmImage, maxModelLen, gpuUtil, hfToken, batchSize, maxSequenceLength, gitRepo, trainingScript, datasetUrl, baseModel } = state;

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div className="space-y-4">
        <label htmlFor="instanceName" className="block text-sm font-medium text-slate-700 dark:text-zinc-300">Deployment Name</label>
        <input id="instanceName" value={instanceName} onChange={e => dispatch({ type: 'SET_FIELD', field: 'instanceName', value: e.target.value })} className="w-full px-4 py-2 border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none transition-all dark:bg-zinc-900 dark:text-white" placeholder="e.g. Production Llama 3" />
      </div>

      <div className="space-y-4">
        <label className="block text-sm font-medium text-slate-700 dark:text-zinc-300">{modelType === "embedding" ? "Embedding Model" : "Model"}</label>
        {selectedHFModel ? (
          <div className="p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
            <div className="flex items-start justify-between">
              <div><div className="font-medium text-blue-900 dark:text-blue-100">{selectedHFModel.id}</div><div className="text-sm text-blue-600 dark:text-blue-400 mt-1">{selectedHFModel.pipeline_tag || "feature-extraction"} • {formatDownloads(selectedHFModel.downloads || 0)} downloads</div></div>
              <button type="button" onClick={() => { dispatch({ type: 'SET_FIELD', field: 'selectedHFModel', value: null }); dispatch({ type: 'SET_FIELD', field: 'modelId', value: "" }); }} className="p-1 hover:bg-blue-100 dark:hover:bg-blue-800 rounded"><X className="w-4 h-4 text-blue-600 dark:text-blue-400" /></button>
            </div>
          </div>
        ) : <HuggingFaceModelBrowser modelType={modelType} onSelect={(m) => { dispatch({ type: 'SET_FIELD', field: 'selectedHFModel', value: m }); dispatch({ type: 'SET_FIELD', field: 'modelId', value: m.id }); }} selectedModelId={modelId} />}
        <input id="modelId" value={modelId} onChange={e => dispatch({ type: 'SET_FIELD', field: 'modelId', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white" placeholder={modelType === "embedding" ? "e.g. sentence-transformers/all-MiniLM-L6-v2" : "e.g. meta-llama/Meta-Llama-3-8B-Instruct"} />
      </div>

      {selectedEngine === "vllm" && modelType === "inference" && (
        <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
          <div className="flex items-center gap-2 mb-2"><Cpu className="w-4 h-4 text-primary" /><h4 className="font-medium text-sm">vLLM Configuration</h4></div>
          <div><label htmlFor="vllmImage" className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">vLLM Image</label><input id="vllmImage" value={vllmImage} onChange={e => dispatch({ type: 'SET_FIELD', field: 'vllmImage', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md bg-white dark:bg-zinc-900 dark:text-white" /></div>
          <div className="grid grid-cols-2 gap-4">
            <div><label htmlFor="maxModelLen" className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Max Model Length</label><input id="maxModelLen" value={maxModelLen} onChange={e => dispatch({ type: 'SET_FIELD', field: 'maxModelLen', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md" /></div>
            <div><label htmlFor="gpuUtil" className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">GPU Memory Util</label><input id="gpuUtil" value={gpuUtil} onChange={e => dispatch({ type: 'SET_FIELD', field: 'gpuUtil', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md" /></div>
          </div>
          <div><label htmlFor="hfTokenVllm" className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">HF Token</label><input id="hfTokenVllm" type="password" value={hfToken} onChange={e => dispatch({ type: 'SET_FIELD', field: 'hfToken', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md" placeholder="hf_..." /></div>
        </div>
      )}

      {modelType === "embedding" && (
        <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
          <div className="flex items-center gap-2 mb-2"><Database className="w-4 h-4 text-primary" /><h4 className="font-medium text-sm">Embedding Configuration</h4></div>
          <div className="grid grid-cols-2 gap-4">
            <div><label htmlFor="batchSize" className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Batch Size</label><input id="batchSize" value={batchSize} onChange={e => dispatch({ type: 'SET_FIELD', field: 'batchSize', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md" /></div>
            <div><label htmlFor="maxSequenceLength" className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Max Sequence Length</label><input id="maxSequenceLength" value={maxSequenceLength} onChange={e => dispatch({ type: 'SET_FIELD', field: 'maxSequenceLength', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md" /></div>
          </div>
        </div>
      )}

      {deploymentType === "training" && (
        <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
          <div className="flex items-center gap-2 mb-2"><Layers className="w-4 h-4 text-primary" /><h4 className="font-medium text-sm">Training Configuration</h4></div>
          <div><label htmlFor="gitRepo" className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Git Repository URL</label><input id="gitRepo" value={gitRepo} onChange={e => dispatch({ type: 'SET_FIELD', field: 'gitRepo', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md" /></div>
          <div><label htmlFor="trainingScript" className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Training Script</label><input id="trainingScript" value={trainingScript} onChange={e => dispatch({ type: 'SET_FIELD', field: 'trainingScript', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md font-mono" /></div>
          <div><label htmlFor="datasetUrl" className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Dataset URL</label><input id="datasetUrl" value={datasetUrl} onChange={e => dispatch({ type: 'SET_FIELD', field: 'datasetUrl', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md" /></div>
        </div>
      )}

      <div className="flex gap-4 pt-6 border-t dark:border-zinc-800"><button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 3 })} className="flex-1 py-2.5 border rounded-md hover:bg-slate-50 font-medium transition-colors text-slate-700 dark:text-zinc-300">Back</button><button type="button" onClick={onLaunch} disabled={isPending} className="flex-[2] py-2.5 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-70 font-medium shadow-sm transition-all flex justify-center items-center gap-2">{isPending ? "Deploying..." : <><Rocket className="w-4 h-4" /> Launch Deployment</>}</button></div>
    </div>
  );
}

function ExternalFlow({ state, dispatch, onLaunch, isPending, filteredProviders, externalModelType }: { state: State; dispatch: React.Dispatch<Action>; onLaunch: () => void; isPending: boolean; filteredProviders: any[]; externalModelType: string }) {
  const { step, selectedProvider, customProviderName, externalModelName, endpointUrl, apiKey, instanceName } = state;

  return (
    <>
      <div className="flex items-center gap-4 text-sm font-medium text-muted-foreground border-b dark:border-zinc-800 pb-4">
        <StepIndicator step={step} current={1} label="Type & Provider" />
        <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
        <StepIndicator step={step} current={2} label="API Configuration" />
        <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
        <StepIndicator step={step} current={3} label="Review & Launch" />
      </div>

      {step === 1 && (
        <div className="space-y-6">
          <div className="flex justify-center"><div className="bg-slate-100 dark:bg-zinc-900 p-1 rounded-lg inline-flex shadow-inner"><button type="button" onClick={() => dispatch({ type: 'SET_FIELD', field: 'modelType', value: 'inference' })} className={cn("px-5 py-2 rounded-md text-sm font-medium transition-all flex items-center gap-2", externalModelType === "inference" ? "bg-white dark:bg-zinc-800 shadow-sm text-blue-600 dark:text-blue-400" : "text-slate-500")}><MessageSquare className="w-4 h-4" /> Inference</button><button type="button" onClick={() => dispatch({ type: 'SET_FIELD', field: 'modelType', value: 'embedding' })} className={cn("px-5 py-2 rounded-md text-sm font-medium transition-all flex items-center gap-2", externalModelType === "embedding" ? "bg-white dark:bg-zinc-800 shadow-sm text-blue-600 dark:text-blue-400" : "text-slate-500")}><Database className="w-4 h-4" /> Embeddings</button></div></div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">{filteredProviders.map(p => (<div key={p.id} role="button" onClick={() => dispatch({ type: 'SET_FIELD', field: 'selectedProvider', value: p.id })} className={cn("cursor-pointer p-6 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 flex items-center gap-4 transition-all outline-none", selectedProvider === p.id ? "border-blue-600 shadow-md" : "hover:border-blue-300")}><div className="p-3 bg-slate-50 dark:bg-zinc-800 rounded-lg"><p.icon className="w-6 h-6 text-slate-700 dark:text-zinc-200" /></div><div><h3 className="font-bold text-lg">{p.name}</h3><p className="text-sm text-slate-500 dark:text-zinc-400">{p.desc}</p></div></div>))}</div>
          <div className="col-span-full flex justify-end pt-4"><button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 2 })} disabled={!selectedProvider} className="px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 transition-colors font-medium">Continue</button></div>
        </div>
      )}

      {step === 2 && (
        <div className="max-w-2xl mx-auto space-y-6 bg-white dark:bg-zinc-900 p-8 rounded-xl border dark:border-zinc-800 shadow-sm">
          {selectedProvider === 'custom' && (<div className="space-y-4"><label htmlFor="customProviderName" className="block text-sm font-medium">Provider Name</label><input id="customProviderName" value={customProviderName} onChange={e => dispatch({ type: 'SET_FIELD', field: 'customProviderName', value: e.target.value })} className="w-full px-4 py-2 border rounded-md dark:bg-zinc-900" placeholder="e.g. My Custom Provider" /></div>)}
          <div className="space-y-4"><label htmlFor="externalModelName" className="block text-sm font-medium">Model Name</label><input id="externalModelName" value={externalModelName} onChange={e => dispatch({ type: 'SET_FIELD', field: 'externalModelName', value: e.target.value })} className="w-full px-4 py-2 border rounded-md dark:bg-zinc-900" placeholder={externalModelType === "embedding" ? "e.g. text-embedding-3" : "e.g. gpt-4o"} /></div>
          <div className="space-y-4"><label htmlFor="apiKey" className="block text-sm font-medium">API Key</label><input id="apiKey" type="password" value={apiKey} onChange={e => dispatch({ type: 'SET_FIELD', field: 'apiKey', value: e.target.value })} className="w-full px-4 py-2 border rounded-md dark:bg-zinc-900 font-mono" placeholder="sk-..." /></div>
          {selectedProvider === 'custom' && (<div className="space-y-4"><label htmlFor="endpointUrl" className="block text-sm font-medium">Endpoint URL</label><input id="endpointUrl" value={endpointUrl} onChange={e => dispatch({ type: 'SET_FIELD', field: 'endpointUrl', value: e.target.value })} className="w-full px-4 py-2 border rounded-md dark:bg-zinc-900" placeholder="https://..." /></div>)}
          <div className="flex justify-between pt-6 border-t dark:border-zinc-800 mt-6"><button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 1 })} className="text-slate-500 font-medium">Back</button><button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 3 })} disabled={!externalModelName || !apiKey || (selectedProvider === 'custom' && (!customProviderName || !endpointUrl))} className="px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 transition-colors font-medium">Continue</button></div>
        </div>
      )}

      {step === 3 && (
        <div className="max-w-xl mx-auto space-y-6">
          <div className="p-6 rounded-xl border dark:border-zinc-800 bg-slate-50/50 dark:bg-zinc-900/50 space-y-4">
            <div className="space-y-2"><label htmlFor="externalInstanceName" className="text-sm font-medium">Name your Deployment</label><input id="externalInstanceName" value={instanceName} onChange={e => dispatch({ type: 'SET_FIELD', field: 'instanceName', value: e.target.value })} className="w-full px-4 py-2 border rounded-md dark:bg-zinc-900" placeholder="My External Model" /></div>
            <div className="pt-4 border-t dark:border-zinc-800 space-y-2 text-sm"><div className="flex justify-between"><span className="text-slate-500">Type</span> <span className="font-medium capitalize">{externalModelType}</span></div><div className="flex justify-between"><span className="text-slate-500">Provider</span> <span className="font-medium capitalize">{selectedProvider}</span></div><div className="flex justify-between"><span className="text-slate-500">Model</span> <span className="font-medium">{externalModelName}</span></div></div>
          </div>
          <div className="flex gap-4"><button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 2 })} className="flex-1 py-2 border rounded-md font-medium text-slate-700 dark:text-zinc-300">Back</button><button type="button" onClick={onLaunch} disabled={isPending} className="flex-[2] py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-70 font-medium shadow-sm flex items-center justify-center gap-2">{isPending ? "Deploying..." : "Launch Deployment"}</button></div>
        </div>
      )}
    </>
  )
}
