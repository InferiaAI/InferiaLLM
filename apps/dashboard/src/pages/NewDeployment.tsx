import { useState, useEffect, useCallback, useReducer } from "react"
import {
  Cpu, Server, Check, Zap, Globe, Layers, Terminal, Rocket, Brain,
  Database, Image, Eye, Volume2, Video, Search, X, Star, Download, Loader2,
  MessageSquare, AlertCircle
} from "lucide-react"
import { computeApi } from "@/lib/api"
import { listPools } from "@/services/poolService"
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
  formatDownloads,
  getModelConfig,
  type HFModel,
  type ModelTypeKey
} from "@/services/huggingfaceService"
import { fetchExternalRegistry, type ExternalModel } from "@/services/gpuCompatibility"
import { resolvePoolGpuResources, extractHfArchitecture, calculatePoolCompatibilityWithFit, mapBestQuantToVllm } from "@/services/modelPlanner"
import { getOllamaModels, searchOllamaModels, formatModelSize, type OllamaModel } from "@/services/ollamaService"
import {
  CompatibilityPanel,
  AutoReplicaConfig,
  GpuSplitConfig,
  VllmConfig,
  EmbeddingConfig,
  DiffusionConfig,
  VllmOmniConfig,
  TrainingConfig,
  PreflightBanner,
} from "@/components/deployment/configSections"
import { ConfigService } from "@/services/configService"
import { buildDiffusionSpec, buildVllmOmniSpec } from "./newDeploymentSpec"

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
    active: true
  },
  {
    id: "video",
    name: "Video Generation",
    desc: "Deploy text-to-video and image-to-video models.",
    icon: Video,
    modelType: "video_generation" as ModelTypeKey,
    active: true
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
    image: "docker.io/vllm/vllm-openai:v0.22.1",
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
    id: "inferia-diffusion",
    name: "Inferia Diffusion",
    desc: "High-performance image & video generation engine powered by Inferia. AWS only.",
    image: "docker.io/inferiaai/inferiadiffusion:latest",
    icon: Image,
    types: ["inference"],
    modelTypes: ["image_generation", "video_generation"]
  },
  {
    id: "vllm-omni",
    name: "Inferia vLLM Omni",
    desc: "Omni-modal vLLM server for image & video generation. AWS only.",
    image: "docker.io/vllm/vllm-omni:v0.23.0",
    icon: Video,
    types: ["inference"],
    modelTypes: ["image_generation", "video_generation"]
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

const geminiModelCatalog = {
  inference: [
    { id: "gemini-3.1-pro-preview", name: "Gemini 3.1 Pro", desc: "Most intelligent model for complex problem-solving.", badge: "New" },
    { id: "gemini-3-flash-preview", name: "Gemini 3 Flash", desc: "Frontier-class performance with high speed and low cost.", badge: "New" },
    { id: "gemini-3.1-flash-lite-preview", name: "Gemini 3.1 Flash-Lite", desc: "Ultra-fast, high-volume model for lightweight tasks.", badge: "New" },
    { id: "gemini-2.5-pro", name: "Gemini 2.5 Pro", desc: "Advanced reasoning and coding capabilities." },
    { id: "gemini-2.5-flash", name: "Gemini 2.5 Flash", desc: "Fast and versatile model for everyday tasks." },
    { id: "gemini-2.5-flash-lite", name: "Gemini 2.5 Flash-Lite", desc: "Cost-efficient model for high-throughput workloads." },
  ],
  embedding: [
    { id: "text-embedding-004", name: "Text Embedding 004", desc: "Latest Gemini text embedding model." },
    { id: "embedding-001", name: "Embedding 001", desc: "General-purpose embedding model." },
  ],
  image_generation: [
    { id: "imagen-4.0-generate-preview", name: "Imagen 4.0", desc: "Google's most advanced image generation model with photorealistic output.", badge: "New" },
    { id: "gemini-2.0-flash-preview-image-generation", name: "Gemini 2.0 Flash (Image Gen)", desc: "Multimodal model with native image generation capabilities.", badge: "New" },
    { id: "imagen-3.0-generate-002", name: "Imagen 3.0", desc: "High-quality image generation with fine-grained control." },
    { id: "imagen-3.0-fast-generate-001", name: "Imagen 3.0 Fast", desc: "Faster image generation optimized for speed." },
  ],
} as const;

const externalProviders = [
  { id: "openai", name: "OpenAI", desc: "GPT + text-embedding models", icon: Globe, defaultEndpoint: "https://api.openai.com", modelTypes: ["inference", "embedding"] as ModelTypeKey[] },
  { id: "gemini", name: "Google Gemini", desc: "Gemini & Imagen models for text, embeddings, and image generation", icon: Globe, defaultEndpoint: "https://generativelanguage.googleapis.com/v1beta/openai", modelTypes: ["inference", "embedding", "image_generation"] as ModelTypeKey[] },
  { id: "anthropic", name: "Anthropic", desc: "Claude chat/completion models", icon: Globe, defaultEndpoint: "https://api.anthropic.com", modelTypes: ["inference"] as ModelTypeKey[] },
  { id: "cohere", name: "Cohere", desc: "Command + embedding models", icon: Globe, defaultEndpoint: "https://api.cohere.ai", modelTypes: ["inference", "embedding"] as ModelTypeKey[] },
  { id: "groq", name: "Groq", desc: "Fast inference via OpenAI-compatible API", icon: Globe, defaultEndpoint: "https://api.groq.com/openai/v1", modelTypes: ["inference"] as ModelTypeKey[] },
  { id: "openrouter", name: "OpenRouter", desc: "Unified API for LLMs and embeddings", icon: Globe, defaultEndpoint: "https://openrouter.ai/api/v1", modelTypes: ["inference", "embedding"] as ModelTypeKey[] },
  { id: "cerebras", name: "Cerebras", desc: "Wafer-scale inference models", icon: Cpu, defaultEndpoint: "https://api.cerebras.ai/v1", modelTypes: ["inference"] as ModelTypeKey[] },
  { id: "custom", name: "Custom OpenAI", desc: "Compatible provider for inference or embeddings", icon: Server, defaultEndpoint: "", modelTypes: ["inference", "embedding"] as ModelTypeKey[] },
]

// --- Types ---

export type State = {
  mode: "managed" | "external";
  step: number;
  deploymentType: string;
  modelType: ModelTypeKey;
  instanceName: string;
  selectedEngine: string;
  selectedPool: any;
  userPools: any[];
  poolsLoading: boolean;
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
  selectedProvider: string;
  customProviderName: string;
  externalModelName: string;
  endpointUrl: string;
  apiKey: string;
  // vLLM runtime hints (used by compatibility widget "Apply Settings")
  dtype: string;
  enforceEager: boolean;
  quantization: string;
  isAdvancedOpen: boolean;
  // Advanced Embedding config
  maxBatchTokens: string;
  pooling: string;
  requiredCpu: string;
  requiredRam: string;
  gpuEnabled: boolean;
  // InferaDiffusion config
  trustRemoteCode: boolean;
  modelOffload: boolean;
  groupOffload: boolean;
  // vLLM AMI + HF token dropdowns
  selectedAmiId: string;
  selectedHfTokenName: string;
  gpuPerReplica: string;
  // Prefill-Decode split configuration
  prefillReplicas: string;
  decodeReplicas: string;
  prefillGpuIndices: string;
  decodeGpuIndices: string;
  isDisaggOpen: boolean;
 
  preflightStatus: 'idle' | 'checking' | 'passed' | 'failed';

  preflightErrors: Array<{ check: string; message: string; needs_hf_token: boolean }>;
  // Auto-replica
  autoReplicaEnabled: boolean;
  tokensPerSecondThreshold: string;
};

export type Action =
  | { type: 'SET_MODE'; payload: "managed" | "external" }
  | { type: 'SET_STEP'; payload: number }
  | { type: 'SET_FIELD'; field: keyof State; value: any }
  | { type: 'INIT_POOLS'; payload: any[] }
  | { type: 'SET_POOLS_LOADING'; payload: boolean }
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
      return { ...state, userPools: action.payload, poolsLoading: false };
    case 'SET_POOLS_LOADING':
      return { ...state, poolsLoading: action.payload };
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
  poolsLoading: false,
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
  maxModelLen: "4192",
  gpuUtil: "0.80",
  hfToken: "",
  selectedProvider: "",
  customProviderName: "",
  externalModelName: "",
  endpointUrl: "",
  apiKey: "",
  // vLLM runtime hints (used by compatibility widget "Apply Settings")
  dtype: "auto",
  enforceEager: true,
  quantization: "",
  isAdvancedOpen: false,
  // Advanced Embedding defaults
  maxBatchTokens: "16384",
  pooling: "cls",
  requiredCpu: "2",
  requiredRam: "4096",
  gpuEnabled: false,
  // InferaDiffusion defaults
  trustRemoteCode: true,
  modelOffload: false,
  groupOffload: false,
  // vLLM AMI + HF token dropdowns
  selectedAmiId: "",
  selectedHfTokenName: "",
  gpuPerReplica: "1",
  // Prefill-Decode split defaults
  prefillReplicas: "0",
  decodeReplicas: "0",
  prefillGpuIndices: "",
  decodeGpuIndices: "",
  isDisaggOpen: false,
 
  preflightStatus: 'idle',

  preflightErrors: [],

  // Auto-replica defaults
  autoReplicaEnabled: false,
  tokensPerSecondThreshold: "10",
};

// --- Pure helpers ---

/**
 * Returns true when the deploy form must require (and show) an Engine AMI.
 * AMI selection is only meaningful for AWS pools; non-AWS vLLM deploys
 * (Nosana, Akash, worker-based) do not provision EC2 instances and therefore
 * have no AMI to select.
 */
export function requiresAmi(engine: string, pool: { provider?: string } | null | undefined): boolean {
  return engine === "vllm" && pool?.provider === "aws";
}

/**
 * Engines that may only be deployed on AWS pools. vLLM Omni and Inferia
 * Diffusion run as GPU containers the worker pulls at runtime; we only support
 * (and validate) them on the AWS provisioning path. Used to filter pool
 * selection and to guard launch.
 */
const AWS_ONLY_ENGINES = new Set(["vllm-omni", "inferia-diffusion"]);

export function requiresAwsPool(engine: string): boolean {
  return AWS_ONLY_ENGINES.has(engine);
}

// --- Components ---

function StepIndicator({ step, current, label }: { step: number; current: number; label: string }) {
  const isActive = step >= current
  return (
    <div className={cn("flex items-center gap-2", isActive && "text-ember-600 dark:text-ember-400")}>
      <div className={cn(
        "w-6 h-6 rounded-full flex items-center justify-center text-xs border transition-colors",
        isActive ? "bg-ember-600 text-white border-ember-600 dark:border-ember-500 dark:bg-ember-600" : "border-border bg-card"
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
        className="w-full px-3 py-2 text-sm border border-dashed border-ember-300 dark:border-ember-700 rounded-md hover:bg-ember-50 dark:hover:bg-ember-900/20 text-ember-600 dark:text-ember-400 flex items-center justify-center gap-2 transition-colors"
      >
        <Search className="w-4 h-4" />
        Browse Hugging Face Models
      </button>
    )
  }

  return (
    <div className="border rounded-lg overflow-hidden bg-card">
      <div className="p-3 border-b dark:border-border flex items-center gap-2">
        <Search className="w-4 h-4 text-muted-foreground" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder={`Search ${MODEL_TYPES[modelType]?.label || "models"} on Hugging Face...`}
          className="flex-1 text-sm outline-none bg-transparent"
        />
        <button
          onClick={() => setShowBrowser(false)}
          className="p-1 hover:bg-muted dark:hover:bg-card rounded"
        >
          <X className="w-4 h-4 text-muted-foreground" />
        </button>
      </div>

      <div className="max-h-64 overflow-y-auto">
        {isLoading ? (
          <div className="p-8 text-center text-muted-foreground">
            <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
            Loading models...
          </div>
        ) : modelType === "embedding" ? (
          <div className="divide-y dark:divide-border">
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
                  "w-full p-3 text-left hover:bg-muted dark:hover:bg-card transition-colors flex items-start gap-3",
                  selectedModelId === model.id && "bg-ember-50 dark:bg-ember-900/20 border-l-2 border-ember-500"
                )}
              >
                <Database className="w-5 h-5 text-muted-foreground mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm truncate">{model.name}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">{model.description}</div>
                  <div className="flex items-center gap-3 mt-1.5 text-xs text-muted-foreground">
                    <span>{model.dimensions}d</span>
                    <span>•</span>
                    <span>Max {model.max_sequence_length} tokens</span>
                  </div>
                </div>
              </button>
            ))}
          </div>
        ) : displayModels?.length ? (
          <div className="divide-y dark:divide-border">
            {displayModels.map((model: HFModel) => (
              <button
                key={model.id}
                onClick={() => {
                  onSelect(model)
                  setShowBrowser(false)
                }}
                className={cn(
                  "w-full p-3 text-left hover:bg-muted dark:hover:bg-card transition-colors",
                  selectedModelId === model.id && "bg-ember-50 dark:bg-ember-900/20 border-l-2 border-ember-500"
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm truncate">{model.id}</div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                      <span className="flex items-center gap-1">
                        <Download className="w-3 h-3" />
                        {formatDownloads(model.downloads || 0)}
                      </span>
                      <span className="flex items-center gap-1">
                        <Star className="w-3 h-3" />
                        {model.likes || 0}
                      </span>
                      {model.pipeline_tag && (
                        <span className="px-1.5 py-0.5 bg-muted dark:bg-card rounded text-[10px]">
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
          <div className="p-8 text-center text-muted-foreground text-sm">
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

function OllamaModelBrowser({
  onSelect,
  selectedModelId,
}: {
  onSelect: (model: HFModel) => void
  selectedModelId: string
}) {
  const [searchQuery, setSearchQuery] = useState("")
  const [showBrowser, setShowBrowser] = useState(false)

  const { data: ollamaModels, isLoading } = useQuery({
    queryKey: ["ollama-models"],
    queryFn: () => getOllamaModels(),
    enabled: showBrowser,
  })

  const filteredModels = searchQuery.length > 0
    ? (ollamaModels || []).filter((m: OllamaModel) => m.name.toLowerCase().includes(searchQuery.toLowerCase()))
    : ollamaModels || []

  if (!showBrowser) {
    return (
      <button
        onClick={() => setShowBrowser(true)}
        className="w-full px-3 py-2 text-sm border border-dashed border-ember-300 dark:border-ember-700 rounded-md hover:bg-ember-50 dark:hover:bg-ember-900/20 text-ember-600 dark:text-ember-400 flex items-center justify-center gap-2 transition-colors"
      >
        <Search className="w-4 h-4" />
        Browse Ollama Models
      </button>
    )
  }

  return (
    <div className="border rounded-lg overflow-hidden bg-card">
      <div className="p-3 border-b dark:border-border flex items-center gap-2">
        <Search className="w-4 h-4 text-muted-foreground" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search Ollama models..."
          className="flex-1 text-sm outline-none bg-transparent"
        />
        <button onClick={() => setShowBrowser(false)} className="p-1 hover:bg-muted dark:hover:bg-card rounded">
          <X className="w-4 h-4 text-muted-foreground" />
        </button>
      </div>

      <div className="max-h-64 overflow-y-auto">
        {isLoading ? (
          <div className="p-8 text-center text-muted-foreground">
            <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />
            Loading Ollama models...
          </div>
        ) : filteredModels.length > 0 ? (
          <div className="divide-y dark:divide-border">
            {filteredModels.map((model: OllamaModel) => (
              <button
                key={model.name}
                onClick={() => {
                  onSelect({
                    id: model.name,
                    modelId: model.name,
                    author: "ollama",
                    lastModified: model.modified_at,
                    tags: ["ollama"],
                    pipeline_tag: "text-generation",
                    downloads: 0,
                    likes: 0,
                    library_name: "ollama",
                  } as HFModel)
                  setShowBrowser(false)
                }}
                className={cn(
                  "w-full p-3 text-left hover:bg-muted dark:hover:bg-card transition-colors",
                  selectedModelId === model.name && "bg-ember-50 dark:bg-ember-900/20 border-l-2 border-ember-500"
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm truncate">{model.name}</div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                      {model.size > 0 && <span>{formatModelSize(model.size)}</span>}
                      {model.details?.parameter_size && <span>{model.details.parameter_size}</span>}
                      {model.details?.family && <span className="px-1.5 py-0.5 bg-muted dark:bg-card rounded text-[10px]">{model.details.family}</span>}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div className="p-8 text-center text-muted-foreground text-sm">
            {searchQuery ? "No Ollama models found for this search." : "No models available."}
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

  const { data: externalRegistry } = useQuery({
    queryKey: ["external-registry"],
    queryFn: fetchExternalRegistry
  })

  const [state, dispatch] = useReducer(deploymentReducer, initialState);
  const {
    mode, step, deploymentType, modelType, instanceName, selectedEngine,
    selectedPool, userPools, selectedHFModel, jobDescription, modelId,
    gitRepo, trainingScript, datasetUrl, baseModel, batchSize,
    maxSequenceLength, maxModelLen, gpuUtil, hfToken,
    selectedProvider, customProviderName, externalModelName, endpointUrl, apiKey,
    // vLLM runtime hints
    dtype, enforceEager, quantization,
    // Advanced Embedding config
    maxBatchTokens, pooling, requiredCpu, requiredRam, gpuEnabled,
    // vLLM AMI + HF token dropdowns
    selectedAmiId, selectedHfTokenName,
  } = state;

  const externalModelType = modelType === "embedding" ? "embedding" : modelType === "image_generation" ? "image_generation" : "inference"
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
      const orgId = user?.org_id || organizations?.[0]?.id;
      if (!orgId) return;
      const fetchPoolList = async () => {
        dispatch({ type: 'SET_POOLS_LOADING', payload: true });
        try {
          const pools = await listPools(orgId);
          const poolish = pools
            .filter((p) => p.lifecycle_state !== "terminated")
            .map((p) => ({
              pool_id: p.pool_id,
              pool_name: p.pool_name,
              provider: p.provider,
              is_active: p.is_active,
              allowed_gpu_types: p.allowed_gpu_types || [],
              gpu_count: p.gpu_count,
              nodes_count: 0,
              lifecycle_state: p.lifecycle_state,
              state: p.lifecycle_state,
            }));
          dispatch({ type: 'INIT_POOLS', payload: poolish });
        } catch (e) {
          console.error("Failed to fetch pools", e)
          toast.error("Failed to list compute pools")
        } finally {
          dispatch({ type: 'SET_POOLS_LOADING', payload: false });
        }
      }
      fetchPoolList()
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
    if ((selectedEngine === "vllm" || selectedEngine === "sglang") && modelType === "inference") {
      const finalModelId = modelId || "meta-llama/Meta-Llama-3-8B-Instruct";
      const spec: any = {
        model_id: finalModelId,
        engine: selectedEngine,
        expose: [{ "port": 9000, "health_checks": [{ "body": JSON.stringify({ model: finalModelId, messages: [{ role: "user", content: "Respond with a single word: Ready" }], stream: false }), "path": "/v1/chat/completions", "type": "http", "method": "POST", "headers": { "Content-Type": "application/json" }, "continuous": false, "expected_status": 200 }] }],
        gpu: true,
      }
      
      if (state.prefillReplicas !== "0" || state.decodeReplicas !== "0") {
        spec.prefill_replicas = parseInt(state.prefillReplicas) || 0;
        spec.decode_replicas = parseInt(state.decodeReplicas) || 0;
        if (state.prefillGpuIndices) spec.prefill_gpu_indices = state.prefillGpuIndices.split(",").map(Number);
        if (state.decodeGpuIndices) spec.decode_gpu_indices = state.decodeGpuIndices.split(",").map(Number);
      }

      return JSON.stringify(spec, null, 4)
    } else if (selectedEngine === "ollama") {
      const finalModelId = modelId || "llama3:8b";
      return JSON.stringify({ model_id: finalModelId, engine: "ollama", image: "ollama/ollama:latest", cmd: ["serve"], expose: [{ port: 11434, type: "http" }], gpu: true }, null, 4)
    } else if (selectedEngine === "infinity") {
      const finalModelId = modelId || "sentence-transformers/all-MiniLM-L6-v2";
      const spec = {
        model_id: finalModelId,
        engine: "infinity",
        image: "michaelf34/infinity:latest",
        port: 7997,
        batch_size: parseInt(batchSize) || 32,
        gpu: gpuEnabled,
        required_cpu: parseInt(requiredCpu) || 2,
        required_ram: parseInt(requiredRam) || 4096,
        env: {
          "INFINITY_MODEL_ID": finalModelId,
          "INFINITY_PORT": "7997",
          ...(hfToken ? { "HF_TOKEN": hfToken } : {})
        },
        expose: [{
          port: 7997,
          type: "http",
          health_checks: [{ path: "/health", type: "http", method: "GET", expected_status: 200 }]
        }]
      }
      return JSON.stringify(spec, null, 4)
    } else if (selectedEngine === "tei") {
      const finalModelId = modelId || "sentence-transformers/all-MiniLM-L6-v2";
      const spec = {
        model_id: finalModelId,
        engine: "tei",
        image: "ghcr.io/huggingface/text-embeddings-inference:latest",
        port: 8080,
        max_batch_tokens: parseInt(maxBatchTokens) || 16384,
        pooling: pooling || "cls",
        gpu: gpuEnabled,
        required_cpu: parseInt(requiredCpu) || 2,
        required_ram: parseInt(requiredRam) || 4096,
        env: hfToken ? { "HF_TOKEN": hfToken } : {},
        expose: [{
          port: 8080,
          type: "http",
          health_checks: [{ path: "/health", type: "http", method: "GET", expected_status: 200 }]
        }]
      }
      return JSON.stringify(spec, null, 4)
    } else if (selectedEngine === "inferia-diffusion") {
      return buildDiffusionSpec({
        modelId,
        modelType,
        trustRemoteCode: state.trustRemoteCode,
        modelOffload: state.modelOffload,
        groupOffload: state.groupOffload,
      })
    } else if (selectedEngine === "vllm-omni") {
      return buildVllmOmniSpec({
        modelId,
        modelType,
        trustRemoteCode: state.trustRemoteCode,
      })
    } else if (selectedEngine === "pytorch") {
      return JSON.stringify({ image: "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime", cmd: ["sleep", "infinity"], gpu: true }, null, 4)
    }
    return ""
  }, [selectedEngine, modelId, modelType, hfToken, batchSize, maxBatchTokens, pooling, requiredCpu, requiredRam, gpuEnabled, state.trustRemoteCode, state.modelOffload, state.groupOffload, state.gpuPerReplica, state.prefillReplicas, state.decodeReplicas, state.prefillGpuIndices, state.decodeGpuIndices])

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
    onError: (err: any) => {
      const body = err.response?.data;
      const msg =
        body?.detail ||
        body?.error?.message ||
        body?.message ||
        "Failed to create deployment";
      toast.error(msg);
    }
  })

  const runPreflight = async (modelId: string, engine: string, token?: string, tokenName?: string): Promise<boolean> => {
    dispatch({ type: 'SET_FIELD', field: 'preflightStatus', value: 'checking' });
    dispatch({ type: 'SET_FIELD', field: 'preflightErrors', value: [] });
    try {
      const { data } = await computeApi.post("/deployment/preflight", {
        model_id: modelId, engine,
        hf_token: token || undefined,
        hf_token_name: tokenName || undefined,
        gpu_per_replica: 1,
        pool_id: selectedPool?.pool_id || undefined,
        model_type: modelType,
      });
      if (data.ready) {
        dispatch({ type: 'SET_FIELD', field: 'preflightStatus', value: 'passed' });
        return true;
      }
      dispatch({ type: 'SET_FIELD', field: 'preflightErrors', value: data.checks.filter((c: any) => !c.passed) });
      dispatch({ type: 'SET_FIELD', field: 'preflightStatus', value: 'failed' });
      return false;
    } catch {
      dispatch({ type: 'SET_FIELD', field: 'preflightStatus', value: 'passed' });
      return true;
    }
  };

  // --- Handlers ---

  const handleManagedLaunch = async () => {
    if (!instanceName) return toast.error("Please name your deployment")
    if (!selectedPool) return toast.error("Select a compute node")
    if (requiresAwsPool(selectedEngine) && selectedPool?.provider !== "aws") return toast.error("This engine can only be deployed on AWS pools.")
    if (requiresAmi(selectedEngine, selectedPool) && !selectedAmiId) return toast.error("Select an engine AMI")
    const targetOrgId = user?.org_id || organizations?.[0]?.id;
    if (!targetOrgId) return toast.error("Organization context missing. Please reload.")

    let config = {}
    try { config = JSON.parse(jobDescription) } catch (e) { return toast.error("Invalid Job JSON specification") }

    // Run preflight checks — forward named HF token for vLLM/SGLang; raw hfToken for other engines
    const preflightOk = await runPreflight(
      modelId || (config as any).model_id || "",
      selectedEngine,
      ["vllm", "sglang", "inferia-diffusion", "vllm-omni"].includes(selectedEngine) ? undefined : (hfToken || undefined),
      ["vllm", "sglang", "inferia-diffusion", "vllm-omni"].includes(selectedEngine) ? (selectedHfTokenName || undefined) : undefined,
    );
    if (!preflightOk) return;
 
    const payload = {
      model_name: instanceName, model_version: "latest", replicas: 1, gpu_per_replica: parseInt(state.gpuPerReplica) || 1, workload_type: deploymentType === "image" ? "inference" : deploymentType, pool_id: selectedPool.pool_id, engine: selectedEngine, model_type: modelType === "image_generation" ? "image_generation" : modelType,
      configuration: deploymentType === "training" ? { workload_type: "training", image: computeEngines.find(e => e.id === selectedEngine)?.image || "pytorch/pytorch:latest", git_repo: gitRepo, training_script: trainingScript, dataset_url: datasetUrl, base_model: baseModel, gpu_count: 1, hf_token: hfToken || undefined } : config,
      owner_id: user?.user_id, org_id: targetOrgId, inference_model: modelId || undefined, job_definition: config,
      ami_id: requiresAmi(selectedEngine, selectedPool) ? selectedAmiId : undefined,
      hf_token_name: ["vllm", "sglang", "inferia-diffusion", "vllm-omni"].includes(selectedEngine) ? (selectedHfTokenName || undefined) : undefined,
      auto_replica_enabled: state.autoReplicaEnabled,
      tokens_per_second_threshold: state.autoReplicaEnabled ? (parseFloat(state.tokensPerSecondThreshold) || undefined) : undefined,
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
    <div className="max-w-4xl mx-auto space-y-8 animate-in fade-in duration-500 font-sans text-foreground">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">New Deployment</h2>
        <p className="text-muted-foreground mt-2">Deploy your models on managed pools or connect to external AI providers.</p>
      </div>

      <div className="flex justify-center">
        <div className="bg-muted dark:bg-card p-1 rounded-lg inline-flex shadow-inner">
          <button
            onClick={() => { dispatch({ type: 'SET_MODE', payload: "managed" }); }}
            className={cn("px-6 py-2.5 rounded-md text-sm font-medium transition-colors flex items-center gap-2", mode === "managed" ? "bg-card shadow-sm text-ember-600 dark:text-ember-400 ring-1 ring-black/5 dark:ring-white/5" : "text-muted-foreground hover:text-foreground dark:text-muted-foreground dark:hover:text-cream/85")}
          >
            <Layers className="w-4 h-4" /> Deploy on Compute
          </button>
          <button
            onClick={() => { dispatch({ type: 'SET_MODE', payload: "external" }); }}
            className={cn("px-6 py-2.5 rounded-md text-sm font-medium transition-colors flex items-center gap-2", mode === "external" ? "bg-card shadow-sm text-ember-600 dark:text-ember-400 ring-1 ring-black/5 dark:ring-white/5" : "text-muted-foreground hover:text-foreground dark:text-muted-foreground dark:hover:text-cream/85")}
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
          externalRegistry={externalRegistry}
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

function ManagedFlow({ state, dispatch, onLaunch, isPending, externalRegistry }: { state: State; dispatch: React.Dispatch<Action>; onLaunch: () => void; isPending: boolean; externalRegistry?: ExternalModel[] }) {
  const { step, deploymentType, modelType, instanceName, selectedEngine, selectedPool, userPools, selectedHFModel, jobDescription, modelId, gitRepo, trainingScript, datasetUrl, baseModel, batchSize, maxSequenceLength, maxModelLen, gpuUtil, hfToken } = state;

  return (
    <>
      <div className="flex items-center gap-4 text-sm font-medium text-muted-foreground border-b dark:border-border pb-4">
        <StepIndicator step={step} current={1} label="Type" />
        <div className="h-px w-8 bg-muted dark:bg-card" />
        <StepIndicator step={step} current={2} label="Engine" />
        <div className="h-px w-8 bg-muted dark:bg-card" />
        <StepIndicator step={step} current={3} label="Node" />
        <div className="h-px w-8 bg-muted dark:bg-card" />
        <StepIndicator step={step} current={4} label="Config" />
      </div>

      {step === 1 && <TypeSelection selectedId={deploymentType} onSelect={(id, mt) => dispatch({ type: 'SELECT_TYPE', deploymentType: id, modelType: mt })} />}
      {step === 2 && <EngineSelection modelType={modelType} selectedEngine={selectedEngine} dispatch={dispatch} setStep={(s) => dispatch({ type: 'SET_STEP', payload: s })} />}
      {step === 3 && <PoolSelection userPools={userPools} poolsLoading={state.poolsLoading} selectedPool={selectedPool} selectedEngine={selectedEngine} dispatch={dispatch} setStep={(s) => dispatch({ type: 'SET_STEP', payload: s })} />}
      {step === 4 && <ManagedConfig state={state} dispatch={dispatch} onLaunch={onLaunch} isPending={isPending} externalRegistry={externalRegistry} />}
    </>
  )
}

function TypeSelection({ selectedId, onSelect }: { selectedId: string; onSelect: (id: string, mt: ModelTypeKey) => void }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {deploymentTypes.map(type => (
        <button
          type="button"
          key={type.id}
          disabled={!type.active}
          aria-pressed={selectedId === type.id}
          onClick={() => type.active && onSelect(type.id, type.modelType)}
          className={cn(
            "w-full p-5 rounded-xl border relative transition-colors outline-none text-left",
            type.active
              ? "cursor-pointer bg-card dark:border-border hover:border-ember-300 dark:hover:border-ember-700 hover:shadow-sm focus:ring-2 focus:ring-ember-500/40"
              : "opacity-50 cursor-not-allowed bg-muted dark:bg-card/50 dark:border-border",
            selectedId === type.id && type.active ? "border-ember-600 dark:border-ember-500 ring-1 ring-ember-600 dark:ring-ember-500 shadow-md" : ""
          )}
        >
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              {type.icon && <type.icon className={cn("w-5 h-5", type.active ? "text-fg-secondary dark:text-cream/85" : "text-muted-foreground")} />}
              <h3 className="font-bold">{type.name}</h3>
            </div>
            {type.badge && <span className="text-[10px] font-bold px-2 py-0.5 bg-muted dark:bg-card text-muted-foreground rounded-full uppercase tracking-wide">{type.badge}</span>}
          </div>
          <p className="text-sm text-muted-foreground leading-relaxed">{type.desc}</p>
        </button>
      ))}
    </div>
  );
}

function EngineSelection({ modelType, selectedEngine, dispatch, setStep }: { modelType: ModelTypeKey; selectedEngine: string; dispatch: React.Dispatch<Action>; setStep: (s: number) => void }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div className="col-span-full">
        <button type="button" onClick={() => setStep(1)} className="text-sm text-muted-foreground hover:text-foreground dark:hover:text-cream/85 font-medium mb-4 flex items-center gap-1">← Back to Type</button>
      </div>
      {computeEngines.filter(e => e.modelTypes.includes(modelType)).map(e => (
        <button type="button" key={e.id} aria-pressed={selectedEngine === e.id} onClick={() => dispatch({ type: 'SET_FIELD', field: 'selectedEngine', value: e.id })} className={cn("w-full cursor-pointer p-6 rounded-xl border bg-card dark:border-border relative transition-colors outline-none text-left focus:ring-2 focus:ring-ember-500/40", selectedEngine === e.id ? "border-ember-600 dark:border-ember-500 ring-1 ring-ember-600 dark:ring-ember-500 shadow-md" : "hover:border-ember-300 dark:hover:border-ember-700 hover:shadow-sm")}>
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              {e.icon && <e.icon className="w-5 h-5 text-fg-secondary dark:text-cream/85" />}
              <h3 className="font-bold text-lg">{e.name}</h3>
            </div>
            {selectedEngine === e.id && <Check className="w-5 h-5 text-ember-600 dark:text-ember-500" />}
          </div>
          <p className="text-sm text-muted-foreground leading-relaxed">{e.desc}</p>
        </button>
      ))}
      <div className="col-span-full flex justify-end pt-4"><button type="button" onClick={() => setStep(3)} className="px-6 py-2 bg-ember-600 text-white rounded-md hover:bg-ember-700 transition-colors font-medium">Continue</button></div>
    </div>
  );
}

function PoolSelection({ userPools, poolsLoading, selectedPool, selectedEngine, dispatch, setStep }: { userPools: any[]; poolsLoading: boolean; selectedPool: any; selectedEngine: string; dispatch: React.Dispatch<Action>; setStep: (s: number) => void }) {
  const awsOnly = requiresAwsPool(selectedEngine);

  // If the engine is AWS-only and a previously-selected pool is non-AWS,
  // clear it so the user can't carry an invalid selection into launch.
  useEffect(() => {
    if (awsOnly && selectedPool && selectedPool.provider !== "aws") {
      dispatch({ type: 'SET_FIELD', field: 'selectedPool', value: null });
    }
  }, [awsOnly, selectedPool, dispatch]);

  const hasSelectablePool = !awsOnly || userPools.some(p => p.provider === "aws" && p.state !== "terminated" && p.state !== "failed");

  return (
    <div className="space-y-6">
      {awsOnly && (
        <div className="flex items-center gap-2 p-3 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded-md text-xs text-amber-700 dark:text-amber-300">
          <AlertCircle className="w-4 h-4 shrink-0" />
          The selected engine is only supported on AWS pools — other providers are disabled.
        </div>
      )}
      {poolsLoading ? (
        <div className="flex items-center justify-center py-12">
          <div className="flex flex-col items-center gap-3">
            <div className="w-8 h-8 border-4 border-ember-500 border-t-transparent rounded-full animate-spin" />
            <p className="text-sm text-muted-foreground">Loading compute pools...</p>
          </div>
        </div>
      ) : userPools.length === 0 || !hasSelectablePool ? (
        <div className="text-center py-12 bg-muted dark:bg-card/50 rounded-xl border border-dashed dark:border-border flex flex-col items-center">
          <Server className="w-12 h-12 text-cream/70 dark:text-muted-foreground mb-4" />
          <h3 className="text-lg font-medium text-foreground dark:text-cream">{awsOnly && userPools.length > 0 ? "No AWS Compute Pools Found" : "No Compute Pools Found"}</h3>
          <p className="text-muted-foreground mt-1 mb-6 max-w-sm">{awsOnly && userPools.length > 0 ? "This engine can only be deployed on AWS pools. Create an AWS pool to continue." : "You need at least one compute pool to deploy this model."}</p>
          <Link to="/dashboard/compute/pools/new" className="px-4 py-2 bg-card border border-border rounded-md text-sm font-medium text-fg-secondary dark:text-cream/70 hover:bg-muted dark:hover:bg-card shadow-sm flex items-center gap-2"><Zap className="w-4 h-4 text-amber-500" /> Create New Pool</Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {userPools.map(pool => {
            const isActive = pool.state === "active" || pool.state === "ready" || pool.state === "idle";
            const isTerminated = pool.state === "terminated" || pool.state === "failed";
            const blockedByProvider = awsOnly && pool.provider !== "aws";
            const selectable = !isTerminated && !blockedByProvider;
            const hasNoNodes = !pool.nodes_count || pool.nodes_count === 0;
            return (
            <button type="button" key={pool.pool_id} aria-pressed={selectedPool?.pool_id === pool.pool_id} disabled={!selectable} onClick={() => selectable && dispatch({ type: 'SET_FIELD', field: 'selectedPool', value: pool })} className={cn("w-full cursor-pointer p-5 rounded-xl border bg-card dark:border-border relative transition-colors outline-none text-left focus:ring-2 focus:ring-ember-500/40", !selectable && "opacity-50 cursor-not-allowed", selectedPool?.pool_id === pool.pool_id ? "border-ember-600 dark:border-ember-500 ring-1 ring-ember-600 dark:ring-ember-500 shadow-md" : "hover:border-ember-300 dark:hover:border-ember-700")}>
              <div className="flex items-start justify-between">
                <div>
                  <div className="font-bold text-lg">{pool.pool_name}</div>
                  <div className="text-sm text-muted-foreground font-mono mt-1">{pool.provider}</div>
                  {blockedByProvider ? (
                    <div className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                      AWS-only engine — not available on {pool.provider}
                    </div>
                  ) : hasNoNodes && (
                    <div className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                      No node yet — deploy will provision (~90s)
                    </div>
                  )}
                </div>
                <div className={cn("px-2 py-0.5 rounded text-xs font-medium border", isActive ? "bg-green-50 text-green-700 border-green-200 dark:bg-green-900/20 dark:text-green-400 dark:border-green-900/50" : "bg-muted text-muted-foreground border-border dark:bg-card dark:text-muted-foreground dark:border-border")}>{isActive ? "Active" : (pool.state || "Inactive")}</div>
              </div>
            </button>
            );
          })}
        </div>
      )}
      <div className="flex justify-between pt-6 border-t dark:border-border"><button type="button" onClick={() => setStep(2)} className="text-muted-foreground hover:text-foreground dark:hover:text-cream/85 font-medium">Back</button><button type="button" onClick={() => selectedPool && setStep(4)} disabled={!selectedPool} className="px-6 py-2 bg-ember-600 text-white rounded-md hover:bg-ember-700 disabled:opacity-50 transition-colors font-medium">Continue</button></div>
    </div>
  );
}

function ManagedConfig({ state, dispatch, onLaunch, isPending, externalRegistry }: { state: State; dispatch: React.Dispatch<Action>; onLaunch: () => void; isPending: boolean; externalRegistry?: ExternalModel[] }) {
  const {
    deploymentType, modelType, instanceName, selectedEngine, selectedHFModel, modelId,
    dtype, quantization, selectedPool, preflightStatus, preflightErrors,
  } = state;

  const { data: hfConfig } = useQuery({
    queryKey: ['modelConfig', modelId],
    queryFn: () => getModelConfig(modelId),
    enabled: !!modelId && (selectedEngine === "vllm" || selectedEngine === "sglang" || selectedEngine === "ollama"),
    staleTime: 1000 * 60 * 60 // 1 hour
  });

  // Engine AMI list — region derived from pool's region_constraint or metadata
  const amiRegion: string =
    selectedPool?.region_constraint?.[0] ||
    selectedPool?.metadata?.region ||
    "us-east-1";

  // AMI selection is only required/shown for AWS pools
  const isAwsPool = requiresAmi(selectedEngine, selectedPool);

  const { data: engineAmis = [], isLoading: amisLoading } = useQuery({
    queryKey: ['engine-amis', amiRegion],
    queryFn: () => ConfigService.listEngineAmis(amiRegion),
    enabled: isAwsPool,
    staleTime: 1000 * 60 * 5,
  });
 
  // HF token names list
  const { data: hfTokenNames = [] } = useQuery({
    queryKey: ['hf-token-names'],
    queryFn: () => ConfigService.listHfTokenNames(),
    enabled: ["vllm", "sglang", "inferia-diffusion", "vllm-omni"].includes(selectedEngine),
    staleTime: 1000 * 60 * 5,
  });

  // Compatibility planning (uses llmfit server when available, falls back to local calculation)
  const { data: compatibility } = useQuery({
    queryKey: ['compat', modelId, selectedPool?.pool_id || selectedPool?.pool_name, quantization, dtype, selectedEngine],
    queryFn: () => calculatePoolCompatibilityWithFit(
      modelId,
      selectedPool,
      hfConfig,
      quantization,
      dtype,
      externalRegistry,
      selectedEngine,
    ),
    enabled: !!selectedPool && !!modelId && (selectedEngine === "vllm" || selectedEngine === "ollama"),
    staleTime: 1000 * 60 * 5,
  });

  // Auto-apply llmfit recommendations when compatibility data arrives
  useEffect(() => {
    if (!compatibility) return;

    if (compatibility.contextLength) {
      dispatch({ type: 'SET_FIELD', field: 'maxModelLen', value: compatibility.contextLength.toString() });
    }

    if (compatibility.bestQuant) {
      const mapped = mapBestQuantToVllm(compatibility.bestQuant);
      if (mapped) {
        dispatch({ type: 'SET_FIELD', field: 'quantization', value: mapped });
      }
    }
  }, [compatibility])

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div className="space-y-4">
        <label htmlFor="instanceName" className="block text-sm font-medium text-fg-secondary dark:text-cream/70">Deployment Name</label>
        <input id="instanceName" value={instanceName} onChange={e => dispatch({ type: 'SET_FIELD', field: 'instanceName', value: e.target.value })} className="w-full px-4 py-2 border dark:border-border rounded-md focus:ring-2 focus:ring-ember-500/20 outline-none transition-colors bg-card dark:text-white" placeholder="e.g. Production Llama 3" />
      </div>

      <div className="space-y-4">
        <label className="block text-sm font-medium text-fg-secondary dark:text-cream/70">{modelType === "embedding" ? "Embedding Model" : "Model"}</label>
        {selectedHFModel ? (
          <div className="p-4 bg-ember-50 dark:bg-ember-900/20 border border-ember-200 dark:border-ember-800 rounded-lg">
            <div className="flex items-start justify-between">
              <div><div className="font-medium text-ember-900 dark:text-ember-100">{selectedHFModel.id}</div><div className="text-sm text-ember-600 dark:text-ember-400 mt-1">{selectedHFModel.pipeline_tag || "feature-extraction"} • {formatDownloads(selectedHFModel.downloads || 0)} downloads</div></div>
              <button type="button" onClick={() => { dispatch({ type: 'SET_FIELD', field: 'selectedHFModel', value: null }); dispatch({ type: 'SET_FIELD', field: 'modelId', value: "" }); }} className="p-1 hover:bg-ember-100 dark:hover:bg-ember-800 rounded"><X className="w-4 h-4 text-ember-600 dark:text-ember-400" /></button>
            </div>
          </div>
        ) : selectedEngine === "ollama" ? (
          <OllamaModelBrowser onSelect={(m) => { dispatch({ type: 'SET_FIELD', field: 'selectedHFModel', value: m }); dispatch({ type: 'SET_FIELD', field: 'modelId', value: m.id }); }} selectedModelId={modelId} />
        ) : (
          <HuggingFaceModelBrowser modelType={modelType} onSelect={(m) => { dispatch({ type: 'SET_FIELD', field: 'selectedHFModel', value: m }); dispatch({ type: 'SET_FIELD', field: 'modelId', value: m.id }); }} selectedModelId={modelId} />
        )}
        <input id="modelId" value={modelId} onChange={e => dispatch({ type: 'SET_FIELD', field: 'modelId', value: e.target.value })} className="w-full px-3 py-2 text-sm border dark:border-border rounded-md focus:ring-2 focus:ring-ember-500/20 outline-none bg-card dark:text-white" placeholder={selectedEngine === "ollama" ? "e.g. llama3:8b, mistral, qwen2" : modelType === "embedding" ? "e.g. sentence-transformers/all-MiniLM-L6-v2" : "e.g. meta-llama/Meta-Llama-3-8B-Instruct"} />
      </div>

      {compatibility && (
        <CompatibilityPanel
          compatibility={compatibility}
          selectedPool={selectedPool}
          selectedEngine={selectedEngine}
          dispatch={dispatch}
        />
      )}

      {/* Auto-Replica Section */}
      {deploymentType === "inference" && <AutoReplicaConfig state={state} dispatch={dispatch} />}

      {/* GPU count slider — only for multi-GPU pools with disagg-capable engines */}
      {selectedPool?.gpu_count > 1 && (selectedEngine === "vllm" || selectedEngine === "sglang") && (
        <GpuSplitConfig state={state} dispatch={dispatch} selectedPool={selectedPool} />
      )}

      {(selectedEngine === "vllm" || selectedEngine === "sglang") && (
        <VllmConfig
          state={state}
          dispatch={dispatch}
          engineAmis={engineAmis}
          amisLoading={amisLoading}
          amiRegion={amiRegion}
          hfTokenNames={hfTokenNames}
        />
      )}

      {modelType === "embedding" && <EmbeddingConfig state={state} dispatch={dispatch} />}

      {selectedEngine === "inferia-diffusion" && (
        <DiffusionConfig state={state} dispatch={dispatch} hfTokenNames={hfTokenNames} />
      )}

      {selectedEngine === "vllm-omni" && (
        <VllmOmniConfig state={state} dispatch={dispatch} hfTokenNames={hfTokenNames} />
      )}

      {deploymentType === "training" && <TrainingConfig state={state} dispatch={dispatch} />}

      <PreflightBanner preflightStatus={preflightStatus} preflightErrors={preflightErrors} />
      <div className="flex gap-4 pt-6 border-t dark:border-border"><button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 3 })} className="flex-1 py-2.5 border rounded-md hover:bg-muted dark:hover:bg-card font-medium transition-colors text-fg-secondary dark:text-cream/70">Back</button><button type="button" onClick={onLaunch} disabled={isPending} className="flex-[2] py-2.5 bg-ember-600 text-white rounded-md hover:bg-ember-700 disabled:opacity-70 font-medium shadow-sm transition-colors flex justify-center items-center gap-2">{isPending ? "Deploying..." : <><Rocket className="w-4 h-4" /> Launch Deployment</>}</button></div>
    </div>
  );
}

function ExternalFlow({ state, dispatch, onLaunch, isPending, filteredProviders, externalModelType }: { state: State; dispatch: React.Dispatch<Action>; onLaunch: () => void; isPending: boolean; filteredProviders: any[]; externalModelType: string }) {
  const { step, selectedProvider, customProviderName, externalModelName, endpointUrl, apiKey, instanceName } = state;
  const [geminiCustomMode, setGeminiCustomMode] = useState(false);
  const catalogModels = selectedProvider === 'gemini' ? (geminiModelCatalog[externalModelType as keyof typeof geminiModelCatalog] ?? []) : [];
  const isInCatalog = catalogModels.some(m => m.id === externalModelName);

  return (
    <>
      <div className="flex items-center gap-4 text-sm font-medium text-muted-foreground border-b dark:border-border pb-4">
        <StepIndicator step={step} current={1} label="Type & Provider" />
        <div className="h-px w-8 bg-muted dark:bg-card" />
        <StepIndicator step={step} current={2} label="API Configuration" />
        <div className="h-px w-8 bg-muted dark:bg-card" />
        <StepIndicator step={step} current={3} label="Review & Launch" />
      </div>

      {step === 1 && (
        <div className="space-y-6">
          <div className="flex justify-center"><div className="bg-muted dark:bg-card p-1 rounded-lg inline-flex shadow-inner"><button type="button" onClick={() => dispatch({ type: 'SET_FIELD', field: 'modelType', value: 'inference' })} className={cn("px-5 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2", externalModelType === "inference" ? "bg-card shadow-sm text-ember-600 dark:text-ember-400" : "text-muted-foreground")}><MessageSquare className="w-4 h-4" /> Inference</button><button type="button" onClick={() => dispatch({ type: 'SET_FIELD', field: 'modelType', value: 'embedding' })} className={cn("px-5 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2", externalModelType === "embedding" ? "bg-card shadow-sm text-ember-600 dark:text-ember-400" : "text-muted-foreground")}><Database className="w-4 h-4" /> Embeddings</button><button type="button" onClick={() => dispatch({ type: 'SET_FIELD', field: 'modelType', value: 'image_generation' })} className={cn("px-5 py-2 rounded-md text-sm font-medium transition-colors flex items-center gap-2", externalModelType === "image_generation" ? "bg-card shadow-sm text-ember-600 dark:text-ember-400" : "text-muted-foreground")}><Image className="w-4 h-4" /> Image Generation</button></div></div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">{filteredProviders.map(p => (<button type="button" key={p.id} aria-pressed={selectedProvider === p.id} onClick={() => dispatch({ type: 'SET_FIELD', field: 'selectedProvider', value: p.id })} className={cn("w-full cursor-pointer p-6 rounded-xl border bg-card dark:border-border flex items-center gap-4 transition-colors outline-none text-left", selectedProvider === p.id ? "border-ember-600 dark:border-ember-500 ring-2 ring-ember-600/20 dark:ring-ember-500/20 bg-ember-50 dark:bg-ember-900/20 shadow-md" : "hover:border-ember-300 dark:hover:border-ember-700")}><div className="p-3 bg-muted dark:bg-card rounded-lg"><p.icon className="w-6 h-6 text-fg-secondary dark:text-cream/85" /></div><div><h3 className="font-bold text-lg">{p.name}</h3><p className="text-sm text-muted-foreground">{p.desc}</p></div></button>))}</div>
          <div className="col-span-full flex justify-end pt-4"><button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 2 })} disabled={!selectedProvider} className="px-6 py-2 bg-ember-600 text-white rounded-md hover:bg-ember-700 disabled:opacity-50 transition-colors font-medium">Continue</button></div>
        </div>
      )}

      {step === 2 && externalModelType === 'image_generation' && (
        <div className="max-w-2xl mx-auto space-y-6 bg-card p-8 rounded-xl border dark:border-border shadow-sm">
          {/* Gemini image model catalog */}
          {selectedProvider === 'gemini' && (
            <div className="space-y-4">
              <label className="block text-sm font-medium">Select an Image Model</label>
              <div className="space-y-2">
                {catalogModels.map(m => (
                  <button
                    type="button"
                    key={m.id}
                    onClick={() => { setGeminiCustomMode(false); dispatch({ type: 'SET_FIELD', field: 'externalModelName', value: m.id }); }}
                    className={cn(
                      "w-full text-left px-4 py-3 rounded-lg border transition-all flex items-center justify-between gap-3",
                      externalModelName === m.id && !geminiCustomMode
                        ? "border-ember-600 dark:border-ember-500 bg-ember-50 dark:bg-ember-900/20 ring-2 ring-ember-600/20 dark:ring-ember-500/20"
                        : "border-border hover:border-ember-300 dark:hover:border-ember-700 bg-card/80"
                    )}
                  >
                    <div className="min-w-0 flex items-center gap-3">
                      <div className="p-2 rounded-lg bg-purple-50 dark:bg-purple-900/20">
                        <Image className="w-4 h-4 text-purple-600 dark:text-purple-400" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm">{m.name}</span>
                          <span className="text-xs text-muted-foreground font-mono">{m.id}</span>
                          {'badge' in m && m.badge && <span className="px-1.5 py-0.5 text-[10px] font-bold uppercase rounded bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-400">{m.badge}</span>}
                        </div>
                        <p className="text-xs text-muted-foreground mt-0.5">{m.desc}</p>
                      </div>
                    </div>
                    <div className="flex-shrink-0">
                      {externalModelName === m.id && !geminiCustomMode ? (
                        <div className="w-5 h-5 rounded-full bg-ember-600 flex items-center justify-center"><Check className="w-3 h-3 text-white" /></div>
                      ) : (
                        <div className="w-5 h-5 rounded-full border-2 border-border" />
                      )}
                    </div>
                  </button>
                ))}
                {/* Custom model option */}
                <button
                  type="button"
                  onClick={() => { setGeminiCustomMode(true); dispatch({ type: 'SET_FIELD', field: 'externalModelName', value: '' }); }}
                  className={cn(
                    "w-full text-left px-4 py-3 rounded-lg border transition-all flex items-center justify-between gap-3",
                    geminiCustomMode
                      ? "border-ember-600 dark:border-ember-500 bg-ember-50 dark:bg-ember-900/20 ring-2 ring-ember-600/20 dark:ring-ember-500/20"
                      : "border-dashed border-border hover:border-ember-300 dark:hover:border-ember-700 bg-card/80"
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <span className="font-medium text-sm">Other Model</span>
                    <p className="text-xs text-muted-foreground mt-0.5">Enter a custom image model ID</p>
                  </div>
                  <div className="flex-shrink-0">
                    {geminiCustomMode ? (
                      <div className="w-5 h-5 rounded-full bg-ember-600 flex items-center justify-center"><Check className="w-3 h-3 text-white" /></div>
                    ) : (
                      <div className="w-5 h-5 rounded-full border-2 border-dashed border-border" />
                    )}
                  </div>
                </button>
                {geminiCustomMode && (
                  <input
                    autoFocus
                    value={externalModelName}
                    onChange={e => dispatch({ type: 'SET_FIELD', field: 'externalModelName', value: e.target.value })}
                    className="w-full px-4 py-2 border rounded-md bg-card dark:text-white border-border text-sm"
                    placeholder="e.g. imagen-3.0-generate-001"
                  />
                )}
              </div>
            </div>
          )}

          {/* Non-Gemini providers: manual model ID input */}
          {selectedProvider !== 'gemini' && (
            <div className="space-y-4">
              <label htmlFor="externalImageModel" className="block text-sm font-medium">Model Name</label>
              <input id="externalImageModel" value={externalModelName} onChange={e => dispatch({ type: 'SET_FIELD', field: 'externalModelName', value: e.target.value })} className="w-full px-4 py-2 border rounded-md bg-card dark:text-white" placeholder="e.g. dall-e-3, stabilityai/stable-diffusion-2-1" />
            </div>
          )}

          <div className="space-y-4">
            <label htmlFor="apiKeyImg" className="block text-sm font-medium">API Key</label>
            <input id="apiKeyImg" type="password" value={apiKey} onChange={e => dispatch({ type: 'SET_FIELD', field: 'apiKey', value: e.target.value })} className="w-full px-4 py-2 border rounded-md bg-card dark:text-white font-mono" placeholder="sk-..." />
          </div>

          {selectedProvider === 'custom' && (
            <div className="space-y-4">
              <label htmlFor="endpointUrlImg" className="block text-sm font-medium">Endpoint URL</label>
              <input id="endpointUrlImg" value={endpointUrl} onChange={e => dispatch({ type: 'SET_FIELD', field: 'endpointUrl', value: e.target.value })} className="w-full px-4 py-2 border rounded-md bg-card dark:text-white" placeholder="https://..." />
            </div>
          )}

          <div className="flex justify-between pt-6 border-t dark:border-border mt-6">
            <button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 1 })} className="text-muted-foreground hover:text-foreground dark:hover:text-cream/85 font-medium">Back</button>
            <button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 3 })} disabled={!externalModelName || !apiKey || (selectedProvider === 'custom' && !endpointUrl)} className="px-6 py-2 bg-ember-600 text-white rounded-md hover:bg-ember-700 disabled:opacity-50 transition-colors font-medium">Continue</button>
          </div>
        </div>
      )}

      {step === 2 && externalModelType !== 'image_generation' && (
        <div className="max-w-2xl mx-auto space-y-6 bg-card p-8 rounded-xl border dark:border-border shadow-sm">
          {selectedProvider === 'custom' && (<div className="space-y-4"><label htmlFor="customProviderName" className="block text-sm font-medium">Provider Name</label><input id="customProviderName" value={customProviderName} onChange={e => dispatch({ type: 'SET_FIELD', field: 'customProviderName', value: e.target.value })} className="w-full px-4 py-2 border rounded-md bg-card dark:text-white" placeholder="e.g. My Custom Provider" /></div>)}

          {/* Gemini model catalog */}
          {selectedProvider === 'gemini' ? (
            <div className="space-y-4">
              <label className="block text-sm font-medium">Select a Model</label>
              <div className="space-y-2">
                {catalogModels.map(m => (
                  <button
                    type="button"
                    key={m.id}
                    onClick={() => { setGeminiCustomMode(false); dispatch({ type: 'SET_FIELD', field: 'externalModelName', value: m.id }); }}
                    className={cn(
                      "w-full text-left px-4 py-3 rounded-lg border transition-all flex items-center justify-between gap-3",
                      externalModelName === m.id && !geminiCustomMode
                        ? "border-ember-600 dark:border-ember-500 bg-ember-50 dark:bg-ember-900/20 ring-2 ring-ember-600/20 dark:ring-ember-500/20"
                        : "border-border hover:border-ember-300 dark:hover:border-ember-700 bg-card/80"
                    )}
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm">{m.name}</span>
                        <span className="text-xs text-muted-foreground font-mono">{m.id}</span>
                        {'badge' in m && m.badge && <span className="px-1.5 py-0.5 text-[10px] font-bold uppercase rounded bg-ember-100 dark:bg-ember-900/40 text-ember-700 dark:text-ember-400">{m.badge}</span>}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">{m.desc}</p>
                    </div>
                    <div className="flex-shrink-0">
                      {externalModelName === m.id && !geminiCustomMode ? (
                        <div className="w-5 h-5 rounded-full bg-ember-600 flex items-center justify-center"><Check className="w-3 h-3 text-white" /></div>
                      ) : (
                        <div className="w-5 h-5 rounded-full border-2 border-border" />
                      )}
                    </div>
                  </button>
                ))}
                {/* Custom model option */}
                <button
                  type="button"
                  onClick={() => { setGeminiCustomMode(true); dispatch({ type: 'SET_FIELD', field: 'externalModelName', value: '' }); }}
                  className={cn(
                    "w-full text-left px-4 py-3 rounded-lg border transition-all flex items-center justify-between gap-3",
                    geminiCustomMode
                      ? "border-ember-600 dark:border-ember-500 bg-ember-50 dark:bg-ember-900/20 ring-2 ring-ember-600/20 dark:ring-ember-500/20"
                      : "border-dashed border-border hover:border-ember-300 dark:hover:border-ember-700 bg-card/80"
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <span className="font-medium text-sm">Other Model</span>
                    <p className="text-xs text-muted-foreground mt-0.5">Enter a custom Gemini model ID</p>
                  </div>
                  <div className="flex-shrink-0">
                    {geminiCustomMode ? (
                      <div className="w-5 h-5 rounded-full bg-ember-600 flex items-center justify-center"><Check className="w-3 h-3 text-white" /></div>
                    ) : (
                      <div className="w-5 h-5 rounded-full border-2 border-dashed border-border" />
                    )}
                  </div>
                </button>
                {geminiCustomMode && (
                  <input
                    autoFocus
                    value={externalModelName}
                    onChange={e => dispatch({ type: 'SET_FIELD', field: 'externalModelName', value: e.target.value })}
                    className="w-full px-4 py-2 border rounded-md bg-card dark:text-white border-border text-sm"
                    placeholder="e.g. gemini-2.0-flash"
                  />
                )}
              </div>
            </div>
          ) : (
            <div className="space-y-4"><label htmlFor="externalModelName" className="block text-sm font-medium">Model Name</label><input id="externalModelName" value={externalModelName} onChange={e => dispatch({ type: 'SET_FIELD', field: 'externalModelName', value: e.target.value })} className="w-full px-4 py-2 border rounded-md bg-card dark:text-white" placeholder={externalModelType === "embedding" ? "e.g. text-embedding-3" : "e.g. gpt-4o"} /></div>
          )}

          <div className="space-y-4"><label htmlFor="apiKey" className="block text-sm font-medium">API Key</label><input id="apiKey" type="password" value={apiKey} onChange={e => dispatch({ type: 'SET_FIELD', field: 'apiKey', value: e.target.value })} className="w-full px-4 py-2 border rounded-md bg-card dark:text-white font-mono" placeholder="sk-..." /></div>
          {selectedProvider === 'custom' && (<div className="space-y-4"><label htmlFor="endpointUrl" className="block text-sm font-medium">Endpoint URL</label><input id="endpointUrl" value={endpointUrl} onChange={e => dispatch({ type: 'SET_FIELD', field: 'endpointUrl', value: e.target.value })} className="w-full px-4 py-2 border rounded-md bg-card dark:text-white" placeholder="https://..." /></div>)}
          <div className="flex justify-between pt-6 border-t dark:border-border mt-6"><button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 1 })} className="text-muted-foreground hover:text-foreground dark:hover:text-cream/85 font-medium">Back</button><button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 3 })} disabled={!externalModelName || !apiKey || (selectedProvider === 'custom' && (!customProviderName || !endpointUrl))} className="px-6 py-2 bg-ember-600 text-white rounded-md hover:bg-ember-700 disabled:opacity-50 transition-colors font-medium">Continue</button></div>
        </div>
      )}

      {step === 3 && (
        <div className="max-w-xl mx-auto space-y-6">
          <div className="p-6 rounded-xl border dark:border-border bg-muted/50 dark:bg-card/50 space-y-4">
            <div className="space-y-2"><label htmlFor="externalInstanceName" className="text-sm font-medium">Name your Deployment</label><input id="externalInstanceName" value={instanceName} onChange={e => dispatch({ type: 'SET_FIELD', field: 'instanceName', value: e.target.value })} className="w-full px-4 py-2 border rounded-md bg-card dark:text-white border-border" placeholder="My External Model" /></div>
            <div className="pt-4 border-t dark:border-border space-y-2 text-sm"><div className="flex justify-between"><span className="text-muted-foreground">Type</span> <span className="font-medium capitalize">{externalModelType}</span></div><div className="flex justify-between"><span className="text-muted-foreground">Provider</span> <span className="font-medium capitalize">{selectedProvider}</span></div><div className="flex justify-between"><span className="text-muted-foreground">Model</span> <span className="font-medium">{externalModelName}</span></div></div>
          </div>
          <div className="flex gap-4"><button type="button" onClick={() => dispatch({ type: 'SET_STEP', payload: 2 })} className="flex-1 py-2 border rounded-md font-medium text-fg-secondary dark:text-cream/70">Back</button><button type="button" onClick={onLaunch} disabled={isPending} className="flex-[2] py-2 bg-ember-600 text-white rounded-md hover:bg-ember-700 disabled:opacity-70 font-medium shadow-sm flex items-center justify-center gap-2">{isPending ? "Deploying..." : "Launch Deployment"}</button></div>
        </div>
      )}
    </>
  )
}
