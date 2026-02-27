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
  id?: string;
  deployment_id?: string;
  model_name?: string;
  provider?: string;
  endpoint_url?: string;
  workload_type?: string;
  git_repo?: string;
  training_script?: string;
  dataset_url?: string;
  engine?: string;
  state?: string;
  status?: string;
  model_type?: string;
  replicas?: number;
  inference_model?: string;
  configuration?: any;
}

type ProviderCapabilities = {
  is_ephemeral: boolean;
  supports_log_streaming: boolean;
  adapter_type: string;
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
          dispatch({ type: 'SET_DEPLOYMENT', payload: { ...data, id: data.deployment_id, name: data.model_name || `Compute-${data.deployment_id.slice(0, 8)}`, provider: data.engine === "vllm" ? "vLLM (Compute)" : "Compute", endpoint_url: data.endpoint, model_name: data.model_name, workload_type: data.configuration?.workload_type || (data.configuration?.git_repo ? "training" : "inference"), git_repo: data.configuration?.git_repo, training_script: data.configuration?.training_script, dataset_url: data.configuration?.dataset_url } });
          return
        }
      } catch { /* ignore */ }
      const { data } = await managementApi.get<DeploymentData[]>("/management/deployments")
      dispatch({ type: 'SET_DEPLOYMENT', payload: data.find((d) => d.id === id) || null });
    } catch (e) { console.error(e) } finally { dispatch({ type: 'SET_LOADING', payload: false }); dispatch({ type: 'SET_PROCESSING', payload: false }); }
  }, [id])

  const { data: providerCapabilities } = useQuery({
    queryKey: ["providerCapabilities", deployment?.provider],
    queryFn: async () => {
      const pId = deployment?.provider?.toLowerCase().replace(" (compute)", "").replace("vllm ", "").trim();
      if (!pId || pId === "compute") return null;
      if (providerCapabilitiesCache[pId]) return providerCapabilitiesCache[pId];
      try {
        const res = await computeApi.get("/inventory/providers")
        const p = res.data.providers[pId]
        if (p) {
          const caps = { is_ephemeral: p.capabilities?.is_ephemeral || false, supports_log_streaming: p.capabilities?.supports_log_streaming || false, adapter_type: p.adapter_type || "cloud" }
          providerCapabilitiesCache[pId] = caps; return caps;
        }
      } catch (e) { console.error(e) }
      return null;
    },
    enabled: !!deployment?.provider,
    staleTime: 5 * 60 * 1000,
  })

  const isCompute = deployment?.engine === "vllm" || providerCapabilities?.is_ephemeral || (deployment?.provider?.toLowerCase() || "").match(/compute|nosana|akash|depin/) !== null

  const deploymentState = (deployment?.state || deployment?.status || "").toUpperCase()
  const isRunning = !["STOPPED", "TERMINATED", "FAILED", "UNKNOWN"].includes(deploymentState)
  const isTraining = deployment?.workload_type === "training"
  const isEmbedding = deployment?.model_type === "embedding" || deployment?.engine === "infinity" || deployment?.engine === "tei"

  const tabs = useMemo(() => {
    const list: { id: TabType; label: string }[] = [{ id: "overview", label: "Overview" }, { id: "logs", label: isTraining ? "Training Logs" : (isEmbedding ? "Embedding Logs" : "Inference Logs") }, { id: "terminal", label: "Terminal Logs" }, { id: "rate_limit", label: "Rate Limits" }, { id: "config", label: "Configuration" }];
    if (!isEmbedding && !isTraining) list.splice(1, 0, { id: "guardrail", label: "Guardrails" }, { id: "rag", label: "RAG & Data" }, { id: "prompt_template", label: "Template" });
    return list.filter((t) => !(t.id === "terminal" && !isCompute))
  }, [isEmbedding, isTraining, isCompute])

  const activeTab: TabType = (tabs.find(t => t.id === searchParams.get("tab"))?.id || "overview") as TabType;

  useEffect(() => { if (id) void fetchDeployment(true) }, [id, fetchDeployment])

  const handleAction = async (action: ActionModalType) => {
    if (!id) return
    dispatch({ type: 'SET_PROCESSING', payload: true });
    try {
      if (action === "start") await computeApi.post("/deployment/start", { deployment_id: id })
      else if (action === "stop") await computeApi.post("/deployment/terminate", { deployment_id: id })
      else if (action === "delete") {
        dispatch({ type: 'SET_DELETING', payload: true });
        if (deployment?.provider?.includes("Compute") || deployment?.engine === "vllm") await computeApi.delete(`/deployment/delete/${id}`)
        else await managementApi.delete(`/management/deployments/${id}`)
        navigate("/dashboard/deployments")
        return
      }
      toast.success(`Deployment ${action}ing...`)
      await fetchDeployment()
    } catch (e) { toast.error(getErrorMessage(e, `Failed to ${action} deployment`)) } finally { dispatch({ type: 'SET_PROCESSING', payload: false }); dispatch({ type: 'SET_DELETING', payload: false }); }
  }

  if (loading) return <LoadingScreen message="Loading deployment details..." />
  if (!deployment && id) return <div className="p-8 text-center">Deployment not found</div>

  return (
    <div className="space-y-6">
      <DeploymentHeader
        deployment={deployment!}
        isRunning={isRunning}
        processing={processing}
        deleting={deleting}
        onRefresh={() => void fetchDeployment(false)}
        onAction={(a) => dispatch({ type: 'SET_ACTION_MODAL', payload: a })}
      />

      <div className="flex gap-1 border-b pb-0">
        {tabs.map((tab) => (
          <button key={tab.id} onClick={() => setSearchParams({ tab: tab.id })} className={cn("px-4 py-2 text-sm font-medium border-b-2 transition-colors", activeTab === tab.id ? "border-primary text-primary bg-muted/20 rounded-t-lg" : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted/10 rounded-t-lg")}>
            {tab.label}
          </button>
        ))}
      </div>

      <TabContent activeTab={activeTab} deployment={deployment!} fetchDeployment={fetchDeployment} />

      {actionModal && <ActionModal type={actionModal} onCancel={() => dispatch({ type: 'SET_ACTION_MODAL', payload: null })} onConfirm={() => { handleAction(actionModal); dispatch({ type: 'SET_ACTION_MODAL', payload: null }); }} />}
    </div>
  )
}

function DeploymentHeader({ deployment, isRunning, processing, deleting, onRefresh, onAction }: { deployment: DeploymentData; isRunning: boolean; processing: boolean; deleting: boolean; onRefresh: () => void; onAction: (a: ActionModalType) => void }) {
  const state = (deployment.state || deployment.status || "").toUpperCase()
  return (
    <div className="flex items-center justify-between">
      <div>
        <h1 className="text-3xl font-bold tracking-tight mb-2">{deployment.model_name || "Deployment"}</h1>
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground text-sm">Provider:</span>
          <span className="px-2 py-0.5 rounded-md bg-muted text-xs font-medium uppercase tracking-wider">{deployment.provider || "Unknown"}</span>
          <div className={cn("flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium uppercase tracking-wider border", isRunning ? "bg-green-500/10 text-green-500 border-green-500/20" : "bg-red-500/10 text-red-500 border-red-500/20")}>
            <div className={cn("w-1.5 h-1.5 rounded-full", isRunning ? "bg-green-50 animate-pulse" : "bg-red-500")} />
            {state}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button onClick={onRefresh} disabled={processing} className="px-4 py-1.5 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-300 rounded-md text-sm font-medium hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center gap-2 disabled:opacity-50 transition-colors"><RefreshCcw className={cn("w-4 h-4", processing && "animate-spin")} /> {processing ? "..." : "Refresh"}</button>
        {isRunning ? (
          <button onClick={() => onAction('stop')} disabled={processing} className="px-4 py-1.5 bg-white dark:bg-zinc-900 border border-amber-500/20 text-amber-600 dark:text-amber-500 rounded-md text-sm font-medium hover:bg-amber-50 dark:hover:bg-amber-500/10 flex items-center gap-2 disabled:opacity-50 transition-colors"><Square className="w-4 h-4" /> Stop</button>
        ) : (
          <div className="flex gap-2">
            <button onClick={() => onAction('start')} disabled={processing} className="px-5 py-1.5 bg-emerald-600 text-white rounded-md text-sm font-semibold hover:bg-emerald-500 flex items-center gap-2 disabled:opacity-50"><Play className="w-4 h-4 fill-current" /> Start</button>
            <button onClick={() => onAction('delete')} disabled={deleting} className="px-4 py-1.5 bg-white dark:bg-zinc-900 border border-red-500/20 text-red-600 dark:text-red-400 rounded-md text-sm font-medium hover:bg-red-50 dark:hover:bg-red-500/10 flex items-center gap-2 disabled:opacity-50 transition-colors"><Trash2 className="w-4 h-4" /> Delete</button>
          </div>
        )}
      </div>
    </div>
  );
}

function TabContent({ activeTab, deployment, fetchDeployment }: { activeTab: TabType; deployment: DeploymentData; fetchDeployment: (ov?: boolean) => void }) {
  const id = deployment.id || deployment.deployment_id;
  if (!id) return null;
  const isTraining = deployment.workload_type === "training"
  const isEmbedding = deployment.model_type === "embedding" || deployment.engine === "infinity" || deployment.engine === "tei"

  switch (activeTab) {
    case "overview": return <DeploymentOverview deployment={deployment} />;
    case "logs": return (
      <div className="bg-card rounded-xl border shadow-sm p-6">
        <h3 className="text-lg font-medium mb-6">{isTraining ? "Training Logs" : isEmbedding ? "Embedding Logs" : "Inference Logs"}</h3>
        {isTraining ? <TrainingLogs deploymentId={id} /> : <InferenceLogs deploymentId={id} />}
      </div>
    );
    case "terminal": return <TerminalLogs deploymentId={id} />;
    case "config": return <DeploymentConfig deployment={deployment} onUpdate={() => fetchDeployment(false)} />;
    case "guardrail": return <DeploymentGuardrails deploymentId={id} />;
    case "rag": return <DeploymentRag deploymentId={id} />;
    case "prompt_template": return <DeploymentPromptTemplate deploymentId={id} />;
    case "rate_limit": return <DeploymentRateLimit deploymentId={id} />;
    default: return null;
  }
}

function ActionModal({ type, onCancel, onConfirm }: { type: ActionModalType; onCancel: () => void; onConfirm: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-xl border bg-background shadow-xl p-5">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 rounded-full bg-amber-500/10 p-2"><AlertTriangle className="h-5 w-5 text-amber-500" /></div>
          <div>
            <h3 className="text-base font-semibold capitalize">{type} deployment?</h3>
            <p className="mt-1 text-sm text-muted-foreground">{type === "delete" ? "This permanently removes the deployment and cannot be undone." : type === "stop" ? "Traffic will stop until you start the deployment again." : "The deployment will be scheduled to start."}</p>
          </div>
        </div>
        <div className="mt-5 flex items-center justify-end gap-2">
          <button type="button" onClick={onCancel} className="px-3 py-1.5 text-sm rounded-md border hover:bg-muted">Cancel</button>
          <button type="button" onClick={onConfirm} className={cn("px-3 py-1.5 text-sm rounded-md text-white", type === "delete" ? "bg-red-600 hover:bg-red-700" : "bg-emerald-600 hover:bg-emerald-700")}>{type === "delete" ? "Delete" : type === "stop" ? "Stop" : "Start"}</button>
        </div>
      </div>
    </div>
  );
}
