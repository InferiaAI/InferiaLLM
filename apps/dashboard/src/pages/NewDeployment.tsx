import { useState, useEffect, useCallback } from "react"
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
  { id: "openai", name: "OpenAI", desc: "GPT-4, GPT-3.5 Turbo", icon: Globe, defaultEndpoint: "https://api.openai.com" },
  { id: "anthropic", name: "Anthropic", desc: "Claude 3 Opus, Sonnet, Haiku", icon: Globe, defaultEndpoint: "https://api.anthropic.com" },
  { id: "cohere", name: "Cohere", desc: "Command R, R+", icon: Globe, defaultEndpoint: "https://api.cohere.ai" },
  { id: "groq", name: "Groq", desc: "LPU Inference Engine", icon: Globe, defaultEndpoint: "https://api.groq.com/openai/v1" },
  { id: "openrouter", name: "OpenRouter", desc: "Unified API for top models", icon: Globe, defaultEndpoint: "https://openrouter.ai/api/v1" },
  { id: "cerebras", name: "Cerebras", desc: "Wafer-Scale AI Inference", icon: Cpu, defaultEndpoint: "https://api.cerebras.ai/v1" },
  { id: "custom", name: "Custom OpenAI", desc: "Compatible with any OpenAI SDK provider", icon: Server, defaultEndpoint: "" },
]

// Hugging Face Model Browser Component
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
  
  // Show curated embedding models for embedding type
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
          autoFocus
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
          // Show curated embedding models
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

export default function NewDeployment() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { user, organizations } = useAuth()

  // --- State ---
  const [mode, setMode] = useState<"managed" | "external">("managed")
  const [step, setStep] = useState(1) // 1: Type, 2: Engine, 3: Pool, 4: Config
  const [deploymentType, setDeploymentType] = useState("inference")
  const [modelType, setModelType] = useState<ModelTypeKey>("inference")

  // Common State
  const [instanceName, setInstanceName] = useState("")

  // Managed Flow State (Pools)
  const [selectedEngine, setSelectedEngine] = useState("vllm")
  const [selectedPool, setSelectedPool] = useState<any>(null)
  const [userPools, setUserPools] = useState<any[]>([])

  // Model Selection State
  const [selectedHFModel, setSelectedHFModel] = useState<HFModel | null>(null)

  // vLLM / Job Config State
  const [jobDescription, setJobDescription] = useState("")
  const [modelId, setModelId] = useState("") // HF Repo ID for vLLM

  // Training State
  const [gitRepo, setGitRepo] = useState("")
  const [trainingScript, setTrainingScript] = useState("python train.py")
  const [datasetUrl, setDatasetUrl] = useState("")
  const [baseModel, setBaseModel] = useState("")

  // Embedding-specific State
  const [embeddingDimensions, setEmbeddingDimensions] = useState("384")
  const [maxSequenceLength, setMaxSequenceLength] = useState("512")
  const [batchSize, setBatchSize] = useState("32")

  const [maxModelLen, setMaxModelLen] = useState("8192")
  const [gpuUtil, setGpuUtil] = useState("0.95")
  const [hfToken, setHfToken] = useState("")
  const [vllmImage, setVllmImage] = useState("docker.io/vllm/vllm-openai:v0.14.0")

  // External Flow State (Direct API)
  const [selectedProvider, setSelectedProvider] = useState("")
  const [customProviderName, setCustomProviderName] = useState("")
  const [externalModelName, setExternalModelName] = useState("")
  const [endpointUrl, setEndpointUrl] = useState("")
  const [apiKey, setApiKey] = useState("")

  // --- Effects & Queries ---

  // Update selected engine when model type changes
  useEffect(() => {
    const availableEngines = computeEngines.filter(e => 
      e.modelTypes.includes(modelType)
    )
    if (availableEngines.length > 0) {
      setSelectedEngine(availableEngines[0].id)
    }
  }, [modelType])

  // Fetch Pools when entering Step 3 of Managed
  useEffect(() => {
    if (mode === "managed" && step === 3) {
      const fetchPools = async () => {
        try {
          const targetOrgId = user?.org_id || organizations?.[0]?.id;
          if (!targetOrgId) {
            toast.error("Organization context missing. Please reload.")
            return
          }

          const res = await computeApi.get(`/deployment/listPools/${targetOrgId}`)
          if (res.data?.pools) {
            setUserPools(res.data.pools)
          }
        } catch (e) {
          console.error("Failed to fetch pools", e)
          toast.error("Failed to list compute pools")
        }
      }
      fetchPools()
    }
  }, [mode, step, user, organizations])

  // Auto-populate endpoint URL when external provider is selected
  useEffect(() => {
    if (mode === "external" && selectedProvider) {
      const provider = externalProviders.find(p => p.id === selectedProvider)
      if (provider?.defaultEndpoint && !endpointUrl) {
        setEndpointUrl(provider.defaultEndpoint)
      }
    }
  }, [selectedProvider, mode])

  // Auto-update modelId when HF model is selected
  useEffect(() => {
    if (selectedHFModel) {
      setModelId(selectedHFModel.id)
    }
  }, [selectedHFModel])

  // vLLM JSON Builder Sync
  useEffect(() => {
    if (selectedEngine === "vllm" && modelType === "inference") {
      const cmd = [
        "--model", modelId || "meta-llama/Meta-Llama-3-8B-Instruct",
        "--served-model-name", modelId || "meta-llama/Meta-Llama-3-8B-Instruct",
        "--port", "9000",
        "--max-model-len", maxModelLen,
        "--gpu-memory-utilization", gpuUtil,
        "--max-num-seqs", "256",
        "--dtype", "auto",
        "--trust-remote-code"
      ]

      const env: any = {}
      if (hfToken) env["HF_TOKEN"] = hfToken

      const expose = [
        {
          "port": 9000,
          "health_checks": [
            {
              "body": JSON.stringify({
                model: modelId || "meta-llama/Meta-Llama-3-8B-Instruct",
                messages: [{ role: "user", content: "Respond with a single word: Ready" }],
                stream: false
              }),
              "path": "/v1/chat/completions",
              "type": "http",
              "method": "POST",
              "headers": {
                "Content-Type": "application/json"
              },
              "continuous": false,
              "expected_status": 200
            }
          ]
        }
      ]

      const spec = {
        image: vllmImage,
        cmd: cmd,
        env: env,
        expose: expose,
        gpu: true
      }
      setJobDescription(JSON.stringify(spec, null, 4))
    } else if (selectedEngine === "ollama") {
      // Always set Ollama template when Ollama is selected
      setJobDescription(JSON.stringify({
        image: "ollama/ollama:latest",
        cmd: ["serve"],
        expose: [{ port: 11434, type: "http" }],
        gpu: true
      }, null, 4))
    } else if (selectedEngine === "infinity") {
      // Infinity embedding server
      const cmd = [
        "v2", 
        "--model-id", modelId || "sentence-transformers/all-MiniLM-L6-v2",
        "--port", "7997"
      ]
      
      const env: any = {
        "INFINITY_MODEL_ID": modelId || "sentence-transformers/all-MiniLM-L6-v2",
        "INFINITY_PORT": "7997"
      }
      if (hfToken) env["HF_TOKEN"] = hfToken

      const spec = {
        image: "michaelf34/infinity:latest",
        cmd: cmd,
        env: env,
        expose: [{
          port: 7997,
          type: "http",
          health_checks: [{
            path: "/health",
            type: "http",
            method: "GET",
            expected_status: 200
          }]
        }],
        gpu: false  // Embeddings can run on CPU
      }
      setJobDescription(JSON.stringify(spec, null, 4))
    } else if (selectedEngine === "tei") {
      // Text Embeddings Inference
      const cmd = [
        "--model-id", modelId || "sentence-transformers/all-MiniLM-L6-v2",
        "--port", "8080"
      ]
      
      const env: any = {}
      if (hfToken) env["HF_TOKEN"] = hfToken

      const spec = {
        image: "ghcr.io/huggingface/text-embeddings-inference:latest",
        cmd: cmd,
        env: env,
        expose: [{
          port: 8080,
          type: "http",
          health_checks: [{
            path: "/health",
            type: "http",
            method: "GET",
            expected_status: 200
          }]
        }],
        gpu: false
      }
      setJobDescription(JSON.stringify(spec, null, 4))
    } else if (selectedEngine === "pytorch") {
      // Training config will be handled separately
      setJobDescription(JSON.stringify({
        image: "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
        cmd: ["sleep", "infinity"],
        gpu: true
      }, null, 4))
    }
  }, [selectedEngine, modelId, maxModelLen, gpuUtil, hfToken, vllmImage, modelType])

  // --- Mutations ---

  const createMutation = useMutation({
    mutationFn: async (payload: any) => {
      await computeApi.post("/deployment/deploy", payload)
    },
    onSuccess: () => {
      toast.success("Deployment created successfully")
      queryClient.invalidateQueries({ queryKey: ["deployments"] })
      navigate("/dashboard/deployments")
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to create deployment")
    }
  })

  // --- Handlers ---

  const handleManagedLaunch = async () => {
    if (!instanceName) return toast.error("Please name your deployment")
    if (!selectedPool) return toast.error("Select a compute pool")

    const targetOrgId = user?.org_id || organizations?.[0]?.id;
    if (!targetOrgId) {
      return toast.error("Organization context missing. Please reload.")
    }

    let config = {}
    try {
      config = JSON.parse(jobDescription)
    } catch (e) {
      return toast.error("Invalid Job JSON specification")
    }

    const payload = {
      model_name: instanceName,
      model_version: "latest",
      replicas: 1,
      gpu_per_replica: modelType === "embedding" ? 0 : 1,  // Embeddings don't need GPU by default
      workload_type: deploymentType,
      pool_id: selectedPool.pool_id,
      engine: selectedEngine,
      model_type: modelType,  // Pass the model type
      configuration: deploymentType === "training" ? {
        // Training Config Payload
        workload_type: "training",
        image: computeEngines.find(e => e.id === selectedEngine)?.image || "pytorch/pytorch:latest",
        git_repo: gitRepo,
        training_script: trainingScript,
        dataset_url: datasetUrl,
        base_model: baseModel,
        gpu_count: 1,
        hf_token: hfToken || undefined
      } : config,
      owner_id: user?.user_id,
      org_id: targetOrgId,
      inference_model: modelId || undefined,
      job_definition: config
    }

    createMutation.mutate(payload)
  }

  const handleExternalLaunch = async () => {
    if (!instanceName) return toast.error("Please name your deployment")
    if (!selectedProvider) return toast.error("Select a provider")
    if (!externalModelName) return toast.error("Enter a model ID")

    const targetOrgId = user?.org_id || organizations?.[0]?.id;
    if (!targetOrgId) {
      return toast.error("Organization context missing. Please reload.")
    }

    const finalProvider = selectedProvider === 'custom' ? customProviderName : selectedProvider

    const config = {
      provider: finalProvider,
      model: externalModelName,
      api_key: apiKey
    }

    const payload = {
      model_name: instanceName,
      model_version: "latest",
      replicas: 1,
      gpu_per_replica: 0,
      workload_type: "external",
      pool_id: "00000000-0000-0000-0000-000000000000",
      engine: finalProvider,
      configuration: config,
      endpoint: endpointUrl || undefined,
      owner_id: user?.user_id,
      org_id: targetOrgId,
      model_type: modelType
    }

    createMutation.mutate(payload)
  }

  // --- Render Helpers ---

  return (
    <div className="max-w-4xl mx-auto space-y-8 animate-in fade-in duration-500 font-sans text-slate-900 dark:text-zinc-50">
      <div>
        <h2 className="text-3xl font-bold tracking-tight">New Deployment</h2>
        <p className="text-muted-foreground mt-2">
          Deploy your models on managed pools or connect to external AI providers.
        </p>
      </div>

      {/* Mode Selection Toggle */}
      <div className="flex justify-center">
        <div className="bg-slate-100 dark:bg-zinc-900 p-1 rounded-lg inline-flex shadow-inner">
          <button
            onClick={() => { setMode("managed"); setStep(1); }}
            className={cn(
              "px-6 py-2.5 rounded-md text-sm font-medium transition-all flex items-center gap-2",
              mode === "managed"
                ? "bg-white dark:bg-zinc-800 shadow-sm text-blue-600 dark:text-blue-400 ring-1 ring-black/5 dark:ring-white/5"
                : "text-slate-500 hover:text-slate-900 dark:text-zinc-400 dark:hover:text-zinc-200"
            )}
          >
            <Layers className="w-4 h-4" /> Deploy on Compute
          </button>
          <button
            onClick={() => { setMode("external"); setStep(1); }}
            className={cn(
              "px-6 py-2.5 rounded-md text-sm font-medium transition-all flex items-center gap-2",
              mode === "external"
                ? "bg-white dark:bg-zinc-800 shadow-sm text-blue-600 dark:text-blue-400 ring-1 ring-black/5 dark:ring-white/5"
                : "text-slate-500 hover:text-slate-900 dark:text-zinc-400 dark:hover:text-zinc-200"
            )}
          >
            <Globe className="w-4 h-4" /> External Provider
          </button>
        </div>
      </div>

      {/* ================= MANAGED FLOW ================= */}
      {mode === "managed" && (
        <>
          {/* Stepper */}
          <div className="flex items-center gap-4 text-sm font-medium text-muted-foreground border-b dark:border-zinc-800 pb-4">
            <StepIndicator step={step} current={1} label="Type" />
            <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
            <StepIndicator step={step} current={2} label="Engine" />
            <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
            <StepIndicator step={step} current={3} label="Pool" />
            <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
            <StepIndicator step={step} current={4} label="Config" />
          </div>

          {/* Step 1: Type Selection */}
          {step === 1 && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {deploymentTypes.map(type => (
                <div
                  key={type.id}
                  onClick={() => {
                    if (type.active) {
                      setDeploymentType(type.id)
                      setModelType(type.modelType)
                      setStep(2)
                    }
                  }}
                  className={cn(
                    "p-5 rounded-xl border relative transition-all",
                    type.active
                      ? "cursor-pointer bg-white dark:bg-zinc-900 dark:border-zinc-800 hover:border-blue-300 dark:hover:border-blue-700 hover:shadow-sm"
                      : "opacity-50 cursor-not-allowed bg-slate-50 dark:bg-zinc-900/50 dark:border-zinc-800",
                    deploymentType === type.id && type.active ? "border-blue-600 dark:border-blue-500 ring-1 ring-blue-600 dark:ring-blue-500 shadow-md" : ""
                  )}
                >
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      {type.icon && <type.icon className={cn("w-5 h-5", type.active ? "text-slate-700 dark:text-zinc-200" : "text-slate-500")} />}
                      <h3 className="font-bold">{type.name}</h3>
                    </div>
                    {type.badge && (
                      <span className="text-[10px] font-bold px-2 py-0.5 bg-slate-200 dark:bg-zinc-800 text-slate-600 dark:text-zinc-400 rounded-full uppercase tracking-wide">
                        {type.badge}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-slate-500 dark:text-zinc-400 leading-relaxed">{type.desc}</p>
                </div>
              ))}
            </div>
          )}

          {/* Step 2: Engine */}
          {step === 2 && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="col-span-full">
                <button onClick={() => setStep(1)} className="text-sm text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200 font-medium mb-4 flex items-center gap-1">← Back to Type</button>
              </div>
              {computeEngines
                .filter(e => e.modelTypes.includes(modelType))
                .map(e => (
                  <div
                    key={e.id}
                    onClick={() => setSelectedEngine(e.id)}
                    className={cn(
                      "cursor-pointer p-6 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 relative transition-all",
                      selectedEngine === e.id ? "border-blue-600 dark:border-blue-500 ring-1 ring-blue-600 dark:ring-blue-500 shadow-md" : "hover:border-blue-300 dark:hover:border-blue-700 hover:shadow-sm"
                    )}
                  >
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
              <div className="col-span-full flex justify-end pt-4">
                <button onClick={() => setStep(3)} className="px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition-colors font-medium">Continue</button>
              </div>
            </div>
          )}

          {/* Step 3: Pool */}
          {step === 3 && (
            <div className="space-y-6">
              {userPools.length === 0 ? (
                <div className="text-center py-12 bg-slate-50 dark:bg-zinc-900/50 rounded-xl border border-dashed dark:border-zinc-800 flex flex-col items-center">
                  <Server className="w-12 h-12 text-slate-300 dark:text-zinc-600 mb-4" />
                  <h3 className="text-lg font-medium text-slate-900 dark:text-zinc-100">No Compute Pools Found</h3>
                  <p className="text-slate-500 dark:text-zinc-400 mt-1 mb-6 max-w-sm">You need active compute resources to deploy this model.</p>
                  <Link to="/dashboard/compute/pools/new" className="px-4 py-2 bg-white dark:bg-zinc-900 border border-slate-300 dark:border-zinc-700 rounded-md text-sm font-medium text-slate-700 dark:text-zinc-300 hover:bg-slate-50 dark:hover:bg-zinc-800 shadow-sm flex items-center gap-2">
                    <Zap className="w-4 h-4 text-amber-500" /> Create New Pool
                  </Link>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {userPools.map(pool => (
                    <div
                      key={pool.pool_id}
                      onClick={() => setSelectedPool(pool)}
                      className={cn(
                        "cursor-pointer p-5 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 relative transition-all",
                        selectedPool?.pool_id === pool.pool_id ? "border-blue-600 dark:border-blue-500 ring-1 ring-blue-600 dark:ring-blue-500 shadow-md" : "hover:border-blue-300 dark:hover:border-blue-700"
                      )}
                    >
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="font-bold text-lg">{pool.pool_name}</div>
                          <div className="text-sm text-slate-500 dark:text-zinc-400 font-mono mt-1">{pool.provider}</div>
                        </div>
                        <div className={cn(
                          "px-2 py-0.5 rounded text-xs font-medium border",
                          pool.is_active
                            ? "bg-green-50 text-green-700 border-green-200 dark:bg-green-900/20 dark:text-green-400 dark:border-green-900/50"
                            : "bg-slate-50 text-slate-500 border-slate-200 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700"
                        )}>
                          {pool.is_active ? "Active" : "Inactive"}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <div className="flex justify-between pt-6 border-t dark:border-zinc-800">
                <button onClick={() => setStep(2)} className="text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200 font-medium">Back</button>
                <button
                  onClick={() => selectedPool && setStep(4)}
                  disabled={!selectedPool}
                  className="px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed font-medium transition-colors"
                >
                  Continue
                </button>
              </div>
            </div>
          )}

          {/* Step 4: Job Config */}
          {step === 4 && (
            <div className="max-w-2xl mx-auto space-y-8">
              <div className="space-y-4">
                <label className="block text-sm font-medium text-slate-700 dark:text-zinc-300">Deployment Name</label>
                <input
                  value={instanceName}
                  onChange={e => setInstanceName(e.target.value)}
                  className="w-full px-4 py-2 border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none transition-all dark:bg-zinc-900 dark:text-white"
                  placeholder="e.g. Production Llama 3"
                  autoFocus
                />
              </div>

              {/* Model Selection - HF Browser */}
              <div className="space-y-4">
                <label className="block text-sm font-medium text-slate-700 dark:text-zinc-300">
                  {modelType === "embedding" ? "Embedding Model" : "Model"}
                </label>
                
                {selectedHFModel ? (
                  <div className="p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg">
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="font-medium text-blue-900 dark:text-blue-100">{selectedHFModel.id}</div>
                        <div className="text-sm text-blue-600 dark:text-blue-400 mt-1">
                          {selectedHFModel.pipeline_tag || "feature-extraction"} • {formatDownloads(selectedHFModel.downloads || 0)} downloads
                        </div>
                      </div>
                      <button
                        onClick={() => {
                          setSelectedHFModel(null)
                          setModelId("")
                        }}
                        className="p-1 hover:bg-blue-100 dark:hover:bg-blue-800 rounded"
                      >
                        <X className="w-4 h-4 text-blue-600 dark:text-blue-400" />
                      </button>
                    </div>
                  </div>
                ) : (
                  <HuggingFaceModelBrowser
                    modelType={modelType}
                    onSelect={(model) => {
                      setSelectedHFModel(model)
                      setModelId(model.id)
                    }}
                    selectedModelId={modelId}
                  />
                )}
                
                <input
                  value={modelId}
                  onChange={e => setModelId(e.target.value)}
                  className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                  placeholder={modelType === "embedding" 
                    ? "e.g. sentence-transformers/all-MiniLM-L6-v2" 
                    : "e.g. meta-llama/Meta-Llama-3-8B-Instruct"
                  }
                />
              </div>

              {/* vLLM Specific Fields */}
              {selectedEngine === "vllm" && modelType === "inference" && (
                <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
                  <div className="flex items-center gap-2 mb-2">
                    <Cpu className="w-4 h-4 text-primary" />
                    <h4 className="font-medium text-sm">vLLM Configuration</h4>
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">vLLM Image</label>
                    <input
                      value={vllmImage}
                      onChange={e => setVllmImage(e.target.value)}
                      className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                      placeholder="e.g. docker.io/vllm/vllm-openai:v0.14.0"
                    />
                    <p className="text-[10px] text-slate-500 mt-1">Docker image for vLLM server (default: v0.14.0)</p>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Max Model Length</label>
                      <input
                        value={maxModelLen}
                        onChange={e => setMaxModelLen(e.target.value)}
                        className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">GPU Memory Util</label>
                      <input
                        value={gpuUtil}
                        onChange={e => setGpuUtil(e.target.value)}
                        className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">HF Token (Optional)</label>
                    <input
                      type="password"
                      value={hfToken}
                      onChange={e => setHfToken(e.target.value)}
                      className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                      placeholder="hf_..."
                    />
                  </div>

                </div>
              )}

              {/* Embedding Specific Fields */}
              {modelType === "embedding" && (
                <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
                  <div className="flex items-center gap-2 mb-2">
                    <Database className="w-4 h-4 text-primary" />
                    <h4 className="font-medium text-sm">Embedding Configuration</h4>
                  </div>

                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Batch Size</label>
                      <input
                        value={batchSize}
                        onChange={e => setBatchSize(e.target.value)}
                        className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                        placeholder="32"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Max Sequence Length</label>
                      <input
                        value={maxSequenceLength}
                        onChange={e => setMaxSequenceLength(e.target.value)}
                        className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                        placeholder="512"
                      />
                    </div>
                  </div>
                  
                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">HF Token (Optional)</label>
                    <input
                      type="password"
                      value={hfToken}
                      onChange={e => setHfToken(e.target.value)}
                      className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                      placeholder="hf_..."
                    />
                  </div>
                </div>
              )}

              {/* Training Specific Fields */}
              {deploymentType === "training" && (
                <div className="space-y-4 p-4 bg-muted/50 rounded-lg border">
                  <div className="flex items-center gap-2 mb-2">
                    <Layers className="w-4 h-4 text-primary" />
                    <h4 className="font-medium text-sm">Training Configuration</h4>
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Git Repository URL</label>
                    <input
                      value={gitRepo}
                      onChange={e => setGitRepo(e.target.value)}
                      className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-green-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                      placeholder="https://github.com/org/repo.git"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Training Command / Script</label>
                    <input
                      value={trainingScript}
                      onChange={e => setTrainingScript(e.target.value)}
                      className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-green-500/20 outline-none font-mono bg-white dark:bg-zinc-900 dark:text-white"
                      placeholder="python train.py --epochs 3"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">Dataset URL (Optional)</label>
                    <input
                      value={datasetUrl}
                      onChange={e => setDatasetUrl(e.target.value)}
                      className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-green-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                      placeholder="https://.../dataset.tar.gz"
                    />
                  </div>

                  <div>
                    <label className="block text-xs font-medium text-slate-600 dark:text-zinc-400 mb-1.5">HF Token (Optional)</label>
                    <input
                      type="password"
                      value={hfToken}
                      onChange={e => setHfToken(e.target.value)}
                      className="w-full px-3 py-2 text-sm border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-green-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                      placeholder="hf_..."
                    />
                  </div>
                </div>
              )}

              <div className="flex gap-4 pt-6 border-t dark:border-zinc-800">
                <button onClick={() => setStep(3)} className="flex-1 py-2.5 border dark:border-zinc-700 rounded-md hover:bg-slate-50 dark:hover:bg-zinc-800 font-medium text-slate-700 dark:text-zinc-300 transition-colors">Back</button>
                <button
                  onClick={handleManagedLaunch}
                  disabled={createMutation.isPending}
                  className="flex-[2] py-2.5 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-70 font-medium shadow-sm transition-all flex justify-center items-center gap-2"
                >
                  {createMutation.isPending ? "Deploying..." : <><Rocket className="w-4 h-4" /> Launch Deployment</>}
                </button>
              </div>
            </div>
          )}
        </>
      )}

      {/* ================= EXTERNAL FLOW ================= */}
      {mode === "external" && (
        <>
          <div className="flex items-center gap-4 text-sm font-medium text-muted-foreground border-b dark:border-zinc-800 pb-4">
            <StepIndicator step={step} current={1} label="Select Provider" />
            <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
            <StepIndicator step={step} current={2} label="API Configuration" />
            <div className="h-px w-8 bg-slate-200 dark:bg-zinc-800" />
            <StepIndicator step={step} current={3} label="Review & Launch" />
          </div>

          {step === 1 && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {externalProviders.map(p => (
                <div
                  key={p.id}
                  onClick={() => setSelectedProvider(p.id)}
                  className={cn(
                    "cursor-pointer p-6 rounded-xl border bg-white dark:bg-zinc-900 dark:border-zinc-800 flex items-center gap-4 transition-all",
                    selectedProvider === p.id ? "border-blue-600 dark:border-blue-500 ring-1 ring-blue-600 dark:ring-blue-500 shadow-md" : "hover:border-blue-300 dark:hover:border-blue-700 hover:shadow-sm"
                  )}
                >
                  <div className="p-3 bg-slate-50 dark:bg-zinc-800 rounded-lg">
                    <p.icon className="w-6 h-6 text-slate-700 dark:text-zinc-200" />
                  </div>
                  <div>
                    <h3 className="font-bold text-lg">{p.name}</h3>
                    <p className="text-sm text-slate-500 dark:text-zinc-400">{p.desc}</p>
                  </div>
                </div>
              ))}
              <div className="col-span-full flex justify-end pt-4">
                <button onClick={() => selectedProvider && setStep(2)} disabled={!selectedProvider} className="px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 transition-colors font-medium">Continue</button>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="max-w-2xl mx-auto space-y-6 bg-white dark:bg-zinc-900 p-8 rounded-xl border dark:border-zinc-800 shadow-sm">
              {selectedProvider === 'custom' && (
                <div className="space-y-4">
                  <label className="block text-sm font-medium text-slate-700 dark:text-zinc-300">Provider Name</label>
                  <input
                    value={customProviderName}
                    onChange={e => setCustomProviderName(e.target.value)}
                    className="w-full px-4 py-2 border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none dark:bg-zinc-900 dark:text-white"
                    placeholder="e.g. My Custom Provider"
                  />
                </div>
              )}

              <div className="space-y-4">
                <label className="block text-sm font-medium text-slate-700 dark:text-zinc-300">Model Name</label>
                <input
                  value={externalModelName}
                  onChange={e => setExternalModelName(e.target.value)}
                  className="w-full px-4 py-2 border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none dark:bg-zinc-900 dark:text-white"
                  placeholder="e.g. gpt-4-turbo"
                />
                <p className="text-xs text-slate-500 dark:text-zinc-400">Check provider docs for exact model identifier.</p>
              </div>

              <div className="space-y-4">
                <label className="block text-sm font-medium text-slate-700 dark:text-zinc-300">API Key</label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={e => setApiKey(e.target.value)}
                  className="w-full px-4 py-2 border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none font-mono text-sm dark:bg-zinc-900 dark:text-white"
                  placeholder="sk-..."
                />
              </div>

              {selectedProvider === 'custom' && (
                <div className="space-y-4">
                  <label className="block text-sm font-medium text-slate-700 dark:text-zinc-300">Endpoint URL</label>
                  <input
                    value={endpointUrl}
                    onChange={e => setEndpointUrl(e.target.value)}
                    className="w-full px-4 py-2 border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none dark:bg-zinc-900 dark:text-white"
                    placeholder="https://api.openai.com/v1"
                  />
                </div>
              )}

              <div className="flex justify-between pt-6 border-t dark:border-zinc-800 mt-6">
                <button onClick={() => setStep(1)} className="text-slate-500 dark:text-zinc-400 hover:text-slate-900 dark:hover:text-zinc-200 font-medium">Back</button>
                <button
                  onClick={() => setStep(3)}
                  disabled={!externalModelName || !apiKey || (selectedProvider === 'custom' && (!customProviderName || !endpointUrl))}
                  className="px-6 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 transition-colors font-medium"
                >
                  Continue
                </button>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="max-w-xl mx-auto space-y-6">
              <div className="p-6 rounded-xl border dark:border-zinc-800 bg-slate-50/50 dark:bg-zinc-900/50 space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Name your Deployment</label>
                  <input
                    autoFocus
                    value={instanceName}
                    onChange={e => setInstanceName(e.target.value)}
                    className="w-full px-4 py-2 border dark:border-zinc-700 rounded-md focus:ring-2 focus:ring-blue-500/20 outline-none bg-white dark:bg-zinc-900 dark:text-white"
                    placeholder="My External Model"
                  />
                </div>
                <div className="pt-4 border-t dark:border-zinc-800 space-y-2 text-sm">
                  <div className="flex justify-between"><span className="text-slate-500 dark:text-zinc-400">Provider</span> <span className="font-medium capitalize">{selectedProvider === 'custom' ? customProviderName : selectedProvider}</span></div>
                  <div className="flex justify-between"><span className="text-slate-500 dark:text-zinc-400">Model</span> <span className="font-medium">{externalModelName}</span></div>
                </div>
              </div>

              <div className="flex gap-4">
                <button onClick={() => setStep(2)} className="flex-1 py-2 border dark:border-zinc-700 rounded-md hover:bg-slate-50 dark:hover:bg-zinc-800 font-medium text-slate-700 dark:text-zinc-300">Back</button>
                <button
                  onClick={handleExternalLaunch}
                  disabled={createMutation.isPending}
                  className="flex-[2] py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-70 font-medium shadow-sm flex items-center justify-center gap-2"
                >
                  {createMutation.isPending ? "Deploying..." : "Launch Deployment"}
                </button>
              </div>
            </div>
          )}
        </>
      )}

    </div>
  )
}

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
