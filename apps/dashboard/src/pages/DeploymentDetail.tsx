import { useCallback, useEffect, useMemo, useReducer } from "react"
import { useNavigate, useParams, useSearchParams } from "react-router-dom"
import { managementApi, computeApi } from "@/lib/api"
import { Trash2, Play, Square, RefreshCcw, AlertTriangle } from "lucide-react"
import { cn } from "@/lib/utils"
import InferenceLogs from "@/components/InferenceLogs"
import TrainingLogs from "@/components/deployment/TrainingLogs"
import DeploymentOverview from "@/components/deployment/DeploymentOverview"
import DeploymentGuardrails from "@/components/deployment/DeploymentGuardrails"
import DeploymentRag from "@/components/deployment/DeploymentRag"
import DeploymentPromptTemplate from "@/components/deployment/DeploymentPromptTemplate"
import DeploymentRateLimit from "@/components/deployment/DeploymentRateLimit"
import TerminalLogs from "@/components/deployment/TerminalLogs"
import DeploymentConfig from "@/components/deployment/DeploymentConfig"
import { toast } from "sonner"
import { useQuery } from "@tanstack/react-query"
import { LoadingScreen } from "@/components/ui/LoadingScreen"

type TabType = "overview" | "logs" | "terminal" | "guardrail" | "rag" | "prompt_template" | "rate_limit" | "config"
type ActionModalType = "start" | "stop" | "delete" | null
type DeploymentData = {
  id?: string
  deployment_id?: string
  model_name?: string
  provider?: string
  endpoint_url?: string
  workload_type?: string
  git_repo?: string
  training_script?: string
  dataset_url?: string
  engine?: string
  state?: string
  status?: string
  model_type?: string
  replicas?: number
  inference_model?: string
  configuration?: any
}

type ProviderCapabilities = {
  is_ephemeral: boolean
  supports_log_streaming: boolean
  adapter_type: string
}

const providerCapabilitiesCache: Record<string, ProviderCapabilities> = {}

type State = {
  loading: boolean;
  deleting: boolean;
  processing: boolean;
  deployment: DeploymentData | null;
  actionModal: ActionModalType;
};

type Action =
  | { type: 'SET_LOADING', payload: boolean }
  | { type: 'SET_DELETING', payload: boolean }
  | { type: 'SET_PROCESSING', payload: boolean }
  | { type: 'SET_DEPLOYMENT', payload: DeploymentData | null }
  | { type: 'SET_ACTION_MODAL', payload: ActionModalType };

const initialState: State = {
  loading: true,
  deleting: false,
  processing: false,
  deployment: null,
  actionModal: null,
};

function deploymentReducer(state: State, action: Action): State {
  switch (action.type) {
    case 'SET_LOADING': return { ...state, loading: action.payload };
    case 'SET_DELETING': return { ...state, deleting: action.payload };
    case 'SET_PROCESSING': return { ...state, processing: action.payload };
    case 'SET_DEPLOYMENT': return { ...state, deployment: action.payload };
    case 'SET_ACTION_MODAL': return { ...state, actionModal: action.payload };
    default: return state;
  }
}

export default function DeploymentDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [state, dispatch] = useReducer(deploymentReducer, initialState);
  const { loading, deleting, processing, deployment, actionModal } = state;

  const getErrorMessage = (error: unknown, fallback: string) => {
    if (typeof error === "object" && error && "response" in error) {
      const response = (error as { response?: { data?: { detail?: string } } }).response
      if (response?.data?.detail) return response.data.detail
    }
    return fallback
  }

  const fetchDeployment = useCallback(async (showOverlay = false) => {
    if (showOverlay) dispatch({ type: 'SET_LOADING', payload: true });
    dispatch({ type: 'SET_PROCESSING', payload: true });

    try {
      try {
        const { data } = await computeApi.get(`/deployment/status/${id}`)
        if (data && data.deployment_id) {
          dispatch({
            type: 'SET_DEPLOYMENT',
            payload: {
              ...data,
              id: data.deployment_id,
              name: data.model_name || `Compute-${data.deployment_id.slice(0, 8)}`,
              provider: data.engine === "vllm" ? "vLLM (Compute)" : "Compute",
              endpoint_url: data.endpoint,
              model_name: data.model_name,
              workload_type: data.configuration?.workload_type || (data.configuration?.git_repo ? "training" : "inference"),
              git_repo: data.configuration?.git_repo,
              training_script: data.configuration?.training_script,
              dataset_url: data.configuration?.dataset_url,
            }
          });
          return
        }
      } catch {
        // Fall back to management API
      }

      const { data } = await managementApi.get<DeploymentData[]>("/management/deployments")
      const found = data.find((d) => d.id === id) || null
      dispatch({ type: 'SET_DEPLOYMENT', payload: found });
    } catch (error) {
      console.error(error)
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false });
      dispatch({ type: 'SET_PROCESSING', payload: false });
    }
  }, [id])

  const { data: providerCapabilities } = useQuery({
    queryKey: ["providerCapabilities", deployment?.provider],
    queryFn: async () => {
      const providerId = deployment?.provider?.toLowerCase().replace(" (compute)", "").replace("vllm ", "").trim()
      if (!providerId || providerId === "compute") return null

      if (providerCapabilitiesCache[providerId]) {
        return providerCapabilitiesCache[providerId]
      }

      try {
        const response = await computeApi.get("/inventory/providers")
        const provider = response.data.providers[providerId]
        if (provider) {
          const capabilities = {
            is_ephemeral: provider.capabilities?.is_ephemeral || false,
            supports_log_streaming: provider.capabilities?.supports_log_streaming || false,
            adapter_type: provider.adapter_type || "cloud",
          }
          providerCapabilitiesCache[providerId] = capabilities
          return capabilities
        }
      } catch (error) {
        console.error("Failed to fetch provider capabilities:", error)
      }

      return null
    },
    enabled: !!deployment?.provider,
    staleTime: 5 * 60 * 1000,
  })

  const isComputeDeployment = () => {
    if (deployment?.engine === "vllm") return true
    if (providerCapabilities?.is_ephemeral) return true

    const provider = deployment?.provider?.toLowerCase() || ""
    return provider.includes("compute") || provider.includes("nosana") || provider.includes("akash") || provider.includes("depin")
  }

  const deploymentState = (deployment?.state || deployment?.status || "").toUpperCase()
  const isStopped = ["STOPPED", "TERMINATED", "FAILED", "UNKNOWN"].includes(deploymentState)
  const isRunning = !isStopped

  const isEmbedding = deployment?.model_type === "embedding" || deployment?.engine === "infinity" || deployment?.engine === "tei"
  const isTraining = deployment?.workload_type === "training"
  const isCompute = isComputeDeployment()

  const tabs = useMemo(() => {
    const tabList: { id: TabType; label: string }[] = [
      { id: "overview", label: "Overview" },
      { id: "logs", label: isTraining ? "Training Logs" : (isEmbedding ? "Embedding Logs" : "Inference Logs") },
      { id: "terminal", label: "Terminal Logs" },
      { id: "rate_limit", label: "Rate Limits" },
      { id: "config", label: "Configuration" },
    ]

    if (!isEmbedding && !isTraining) {
      tabList.splice(
        1,
        0,
        { id: "guardrail", label: "Guardrails" },
        { id: "rag", label: "RAG & Data" },
        { id: "prompt_template", label: "Template" }
      )
    }

    return tabList.filter((tab) => !(tab.id === "terminal" && !isCompute))
  }, [isEmbedding, isTraining, isCompute])

  const currentTabParam = searchParams.get("tab") as TabType | null
  const activeTab: TabType = tabs.some((tab) => tab.id === currentTabParam) ? (currentTabParam as TabType) : "overview"

  useEffect(() => {
    if (id) {
      void fetchDeployment(true)
    }
  }, [id, fetchDeployment])

  useEffect(() => {
    if (currentTabParam && !tabs.some((tab) => tab.id === currentTabParam)) {
      const next = new URLSearchParams(searchParams)
      next.set("tab", "overview")
      setSearchParams(next, { replace: true })
    }
  }, [currentTabParam, tabs, searchParams, setSearchParams])

  const handleStop = async () => {
    if (!id) return
    dispatch({ type: 'SET_PROCESSING', payload: true });
    try {
      await computeApi.post("/deployment/terminate", { deployment_id: id })
      toast.success("Deployment stopping...")
      await fetchDeployment()
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, "Failed to stop deployment"))
    } finally {
      dispatch({ type: 'SET_PROCESSING', payload: false });
    }
  }

  const handleStart = async () => {
    if (!id) return
    dispatch({ type: 'SET_PROCESSING', payload: true });
    try {
      await computeApi.post("/deployment/start", { deployment_id: id })
      toast.success("Deployment starting...")
      await fetchDeployment()
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, "Failed to start deployment"))
    } finally {
      dispatch({ type: 'SET_PROCESSING', payload: false });
    }
  }

  const handleDelete = async () => {
    if (!id) return
    dispatch({ type: 'SET_DELETING', payload: true });
    try {
      const isCompute = deployment?.provider?.includes("Compute") || deployment?.engine === "vllm"
      if (isCompute) {
        await computeApi.delete(`/deployment/delete/${id}`)
      } else {
        await managementApi.delete(`/management/deployments/${id}`)
      }
      toast.success("Deployment deleted successfully")
      navigate("/dashboard/deployments")
    } catch (error: unknown) {
      toast.error(getErrorMessage(error, "Failed to delete deployment"))
    } finally {
      dispatch({ type: 'SET_DELETING', payload: false });
    }
  }

  const handleConfirmAction = async () => {
    const selectedAction = actionModal
    dispatch({ type: 'SET_ACTION_MODAL', payload: null });

    if (selectedAction === "start") {
      await handleStart()
      return
    }
    if (selectedAction === "stop") {
      await handleStop()
      return
    }
    if (selectedAction === "delete") {
      await handleDelete()
    }
  }

  if (loading) return <LoadingScreen message="Loading deployment details..." />
  if (!deployment && id) return <div>Deployment not found</div>

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight mb-2">{deployment?.model_name || "Deployment"}</h1>
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-sm">Provider:</span>
            <span className="px-2 py-0.5 rounded-md bg-muted text-xs font-medium uppercase tracking-wider">{deployment?.provider || "Unknown"}</span>
            <div
              className={cn(
                "flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium uppercase tracking-wider border",
                isRunning ? "bg-green-500/10 text-green-500 border-green-500/20" : "bg-red-500/10 text-red-500 border-red-500/20"
              )}
            >
              <div className={cn("w-1.5 h-1.5 rounded-full", isRunning ? "bg-green-500 animate-pulse" : "bg-red-500")} />
              {deploymentState || "Unknown"}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => void fetchDeployment(false)}
            disabled={processing}
            className="px-4 py-1.5 bg-zinc-900 border border-zinc-700 text-zinc-300 rounded-md text-sm font-medium hover:bg-zinc-800 hover:text-zinc-100 hover:border-zinc-600 flex items-center gap-2 transition-all active:scale-95 disabled:opacity-50"
          >
            <RefreshCcw className={cn("w-4 h-4", processing && "animate-spin")} /> {processing ? "Refreshing..." : "Refresh"}
          </button>

          {isRunning ? (
            <button
              onClick={() => dispatch({ type: 'SET_ACTION_MODAL', payload: 'stop' })}
              disabled={processing}
              className="px-4 py-1.5 bg-zinc-900 border border-amber-500/20 text-amber-500 rounded-md text-sm font-medium hover:bg-amber-500/10 hover:border-amber-500/40 flex items-center gap-2 transition-all disabled:opacity-50"
            >
              <Square className="w-4 h-4" /> {processing ? "Stopping..." : "Stop"}
            </button>
          ) : (
            <>
              <button
                onClick={() => dispatch({ type: 'SET_ACTION_MODAL', payload: 'start' })}
                disabled={processing}
                className="px-5 py-1.5 bg-blue-600 text-white rounded-md text-sm font-semibold hover:bg-blue-500 active:scale-95 flex items-center gap-2 transition-all disabled:opacity-50"
              >
                <Play className="w-4 h-4 fill-current" /> {processing ? "Starting..." : "Start"}
              </button>
              <button
                onClick={() => dispatch({ type: 'SET_ACTION_MODAL', payload: 'delete' })}
                disabled={deleting}
                className="px-4 py-1.5 bg-zinc-900 border border-red-500/20 text-red-400 rounded-md text-sm font-medium hover:bg-red-500/10 hover:border-red-500/40 flex items-center gap-2 transition-all disabled:opacity-50"
              >
                <Trash2 className="w-4 h-4" /> {deleting ? "Deleting..." : "Delete"}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="flex gap-1 border-b pb-0">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => {
              const next = new URLSearchParams(searchParams)
              next.set("tab", tab.id)
              setSearchParams(next)
            }}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 transition-colors",
              activeTab === tab.id
                ? "border-primary text-primary bg-muted/20 rounded-t-lg"
                : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted/10 rounded-t-lg"
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "overview" && deployment && <DeploymentOverview deployment={deployment} />}

      {activeTab === "logs" && id && (
        <div className="bg-card rounded-xl border shadow-sm p-6">
          <h3 className="text-lg font-medium mb-6">{isTraining ? "Training Logs" : isEmbedding ? "Embedding Logs" : "Inference Logs"}</h3>
          {isTraining ? <TrainingLogs deploymentId={id} /> : <InferenceLogs deploymentId={id} />}
        </div>
      )}

      {activeTab === "terminal" && id && (
        <div className="space-y-4">
          <TerminalLogs deploymentId={id} />
        </div>
      )}

      {activeTab === "config" && deployment && (
        <DeploymentConfig
          deployment={deployment}
          onUpdate={() => void fetchDeployment(false)}
        />
      )}

      {activeTab === "guardrail" && id && <DeploymentGuardrails deploymentId={id} />}
      {activeTab === "rag" && id && <DeploymentRag deploymentId={id} />}
      {activeTab === "prompt_template" && id && <DeploymentPromptTemplate deploymentId={id} />}
      {activeTab === "rate_limit" && id && <DeploymentRateLimit deploymentId={id} />}

      {actionModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
          <div className="w-full max-w-md rounded-xl border bg-background shadow-xl p-5">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 rounded-full bg-amber-500/10 p-2">
                <AlertTriangle className="h-5 w-5 text-amber-500" />
              </div>
              <div>
                <h3 className="text-base font-semibold">
                  {actionModal === "delete" ? "Delete deployment?" : actionModal === "stop" ? "Stop deployment?" : "Start deployment?"}
                </h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  {actionModal === "delete"
                    ? "This permanently removes the deployment and cannot be undone."
                    : actionModal === "stop"
                      ? "Traffic will stop until you start the deployment again."
                      : "The deployment will be scheduled to start."}
                </p>
              </div>
            </div>

            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => dispatch({ type: 'SET_ACTION_MODAL', payload: null })}
                className="px-3 py-1.5 text-sm rounded-md border hover:bg-muted"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleConfirmAction()}
                className={cn(
                  "px-3 py-1.5 text-sm rounded-md text-white",
                  actionModal === "delete" ? "bg-red-600 hover:bg-red-700" : "bg-blue-600 hover:bg-blue-700"
                )}
              >
                {actionModal === "delete" ? "Delete" : actionModal === "stop" ? "Stop" : "Start"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
