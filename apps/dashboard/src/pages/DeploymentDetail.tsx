import { useEffect, useState } from "react"
import { useParams, Link, useNavigate } from "react-router-dom"
import { managementApi, computeApi } from "@/lib/api"
import { Activity, Gauge, Trash2, Play, Square, RefreshCcw } from "lucide-react"
import { cn } from "@/lib/utils"
import InferenceLogs from "@/components/InferenceLogs"
import TrainingLogs from "@/components/deployment/TrainingLogs"
import DeploymentOverview from "@/components/deployment/DeploymentOverview"
import DeploymentGuardrails from "@/components/deployment/DeploymentGuardrails"
import DeploymentRag from "@/components/deployment/DeploymentRag"
import DeploymentPromptTemplate from "@/components/deployment/DeploymentPromptTemplate"
import DeploymentRateLimit from "@/components/deployment/DeploymentRateLimit"
import TerminalLogs from "@/components/deployment/TerminalLogs"
import { toast } from "sonner"
import { useQuery } from "@tanstack/react-query"

type TabType = "overview" | "logs" | "terminal" | "guardrail" | "rag" | "prompt_template" | "rate_limit"

import { LoadingScreen } from "@/components/ui/LoadingScreen"

// NEW: Provider capabilities cache
type ProviderCapabilities = {
    is_ephemeral: boolean;
    supports_log_streaming: boolean;
    adapter_type: string;
}

const providerCapabilitiesCache: Record<string, ProviderCapabilities> = {}



export default function DeploymentDetail() {
    const { id } = useParams<{ id: string }>()
    const navigate = useNavigate()
    const [activeTab, setActiveTab] = useState<TabType>("overview")
    const [loading, setLoading] = useState(true)
    const [deleting, setDeleting] = useState(false)

    // Deployment Info
    const [deployment, setDeployment] = useState<any>(null)

    const fetchDeployment = async (showOverlay = false) => {
        if (showOverlay) setLoading(true)
        setProcessing(true)
        try {
            // 1. Try fetching from Compute Orchestrator directly
            try {
                const { data } = await computeApi.get(`/deployment/status/${id}`)
                if (data && data.deployment_id) {
                    setDeployment({
                        ...data,
                        id: data.deployment_id,
                        name: data.model_name || `Compute-${data.deployment_id.slice(0, 8)}`,
                        provider: data.engine === "vllm" ? "vLLM (Compute)" : "Compute",
                        endpoint_url: data.endpoint,
                        model_name: data.model_name, // Ensure field matches UI expectation

                        // Training Specific Extraction
                        workload_type: data.configuration?.workload_type || (data.configuration?.git_repo ? "training" : "inference"),
                        git_repo: data.configuration?.git_repo,
                        training_script: data.configuration?.training_script,
                        dataset_url: data.configuration?.dataset_url,
                    })
                    setLoading(false)
                    return
                }
            } catch (err) {
                // Ignore and try management API
            }

            // 2. Fallback to Management API (External Deployments)
            // In a real app we'd have a single GET /management/deployments/:id endpoint
            // For now we just find it in the list or assume it exists if we navigated here
            const { data } = await managementApi.get("/management/deployments")
            const found = data.find((d: any) => d.id === id)
            setDeployment(found)
        } catch (e) {
            console.error(e)
        } finally {
            setLoading(false)
            setProcessing(false)
        }
    }

    const [processing, setProcessing] = useState(false)

    // NEW: Fetch provider capabilities for the deployment's provider
    const { data: providerCapabilities } = useQuery({
        queryKey: ["providerCapabilities", deployment?.provider],
        queryFn: async () => {
            const providerId = deployment?.provider?.toLowerCase().replace(" (compute)", "").replace("vllm ", "").trim()
            if (!providerId || providerId === "compute") return null
            
            // Check cache first
            if (providerCapabilitiesCache[providerId]) {
                return providerCapabilitiesCache[providerId]
            }
            
            try {
                const res = await computeApi.get('/inventory/providers')
                const provider = res.data.providers[providerId]
                if (provider) {
                    const caps = {
                        is_ephemeral: provider.capabilities?.is_ephemeral || false,
                        supports_log_streaming: provider.capabilities?.supports_log_streaming || false,
                        adapter_type: provider.adapter_type || 'cloud'
                    }
                    providerCapabilitiesCache[providerId] = caps
                    return caps
                }
            } catch (e) {
                console.error("Failed to fetch provider capabilities:", e)
            }
            return null
        },
        enabled: !!deployment?.provider,
        staleTime: 5 * 60 * 1000, // Cache for 5 minutes
    })

    const isStopped = ["STOPPED", "TERMINATED", "FAILED", "unknown"].includes(deployment?.state || deployment?.status || "")
    const isRunning = !isStopped
    
    // NEW: Determine if this is a compute deployment using capabilities
    const isComputeDeployment = () => {
        // Check if engine indicates compute
        if (deployment?.engine === "vllm") return true
        
        // Check if provider is ephemeral (DePIN) using capabilities
        if (providerCapabilities?.is_ephemeral) return true
        
        // Fallback to legacy checks for backward compatibility
        const provider = deployment?.provider?.toLowerCase() || ""
        return provider.includes("compute") || 
               provider.includes("nosana") || 
               provider.includes("akash") ||
               provider.includes("depin")
    }

    const handleStop = async () => {
        if (!id || !confirm("Are you sure you want to stop this deployment?")) return
        setProcessing(true)
        try {
            await computeApi.post("/deployment/terminate", { deployment_id: id })
            toast.success("Deployment stopping...")
            fetchDeployment()
        } catch (err: any) {
            toast.error(err.response?.data?.detail || "Failed to stop deployment")
        } finally {
            setProcessing(false)
        }
    }

    const handleStart = async () => {
        if (!id) return
        setProcessing(true)
        try {
            await computeApi.post("/deployment/start", { deployment_id: id })
            toast.success("Deployment starting...")
            fetchDeployment()
        } catch (err: any) {
            toast.error(err.response?.data?.detail || "Failed to start deployment")
        } finally {
            setProcessing(false)
        }
    }

    const handleDelete = async () => {
        if (!id || !confirm("Are you sure you want to permanently delete this deployment?")) return
        setDeleting(true)
        try {
            const isCompute = deployment?.provider?.includes("Compute") || deployment?.engine === "vllm"
            if (isCompute) {
                await computeApi.delete(`/deployment/delete/${id}`)
            } else {
                await managementApi.delete(`/management/deployments/${id}`)
            }
            toast.success("Deployment deleted successfully")
            navigate("/dashboard/deployments")
        } catch (err: any) {
            toast.error(err.response?.data?.detail || "Failed to delete deployment")
        } finally {
            setDeleting(false)
        }
    }

    useEffect(() => {
        if (id) fetchDeployment(true)
    }, [id])


    if (loading) return <LoadingScreen message="Loading deployment details..." />
    if (!deployment && !id) return <div>Deployment not found</div>

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight mb-2">{deployment?.model_name || "Deployment"}</h1>
                    <div className="flex items-center gap-2">
                        <span className="text-muted-foreground text-sm">Provider:</span>
                        <span className="px-2 py-0.5 rounded-md bg-muted text-xs font-medium uppercase tracking-wider">{deployment?.provider || "Unknown"}</span>
                        <div className={cn(
                            "flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium uppercase tracking-wider border",
                            isRunning ? "bg-green-500/10 text-green-500 border-green-500/20" : "bg-red-500/10 text-red-500 border-red-500/20"
                        )}>
                            <div className={cn("w-1.5 h-1.5 rounded-full", isRunning ? "bg-green-500 animate-pulse" : "bg-red-500")} />
                            {deployment?.state || "Unknown"}
                        </div>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => fetchDeployment(false)}
                        disabled={processing}
                        className="px-4 py-1.5 bg-zinc-900 border border-zinc-700 text-zinc-300 rounded-md text-sm font-medium hover:bg-zinc-800 hover:text-zinc-100 hover:border-zinc-600 flex items-center gap-2 transition-all active:scale-95 disabled:opacity-50"
                    >
                        <RefreshCcw className={cn("w-4 h-4", processing && "animate-spin")} /> {processing ? "Refreshing..." : "Refresh"}
                    </button>

                    {isRunning ? (
                        <button
                            onClick={handleStop}
                            disabled={processing}
                            className="px-4 py-1.5 bg-zinc-900 border border-amber-500/20 text-amber-500 rounded-md text-sm font-medium hover:bg-amber-500/10 hover:border-amber-500/40 flex items-center gap-2 transition-all disabled:opacity-50 shadow-[0_0_15px_rgba(245,158,11,0.05)]"
                        >
                            <Square className="w-4 h-4" /> {processing ? "Stopping..." : "Stop"}
                        </button>
                    ) : (
                        <>
                            <button
                                onClick={handleStart}
                                disabled={processing}
                                className="px-5 py-1.5 bg-blue-600 text-white rounded-md text-sm font-semibold hover:bg-blue-500 active:scale-95 flex items-center gap-2 transition-all disabled:opacity-50 shadow-[0_0_20px_rgba(37,99,235,0.2)]"
                            >
                                <Play className="w-4 h-4 fill-current" /> {processing ? "Starting..." : "Start"}
                            </button>
                            <button
                                onClick={handleDelete}
                                disabled={deleting}
                                className="px-4 py-1.5 bg-zinc-900 border border-red-500/20 text-red-400 rounded-md text-sm font-medium hover:bg-red-500/10 hover:border-red-500/40 flex items-center gap-2 transition-all disabled:opacity-50"
                            >
                                <Trash2 className="w-4 h-4" /> {deleting ? "Deleting..." : "Delete"}
                            </button>
                        </>
                    )}
                </div>
            </div>

            {/* Tabs */}
            <div className="flex gap-1 border-b pb-0">
                {[
                    { id: "overview", label: "Overview" },
                    { id: "guardrail", label: "Guardrails" },
                    { id: "rag", label: "RAG & Data" },
                    { id: "logs", label: "Inference Logs" },
                    { id: "terminal", label: "Terminal Logs" },
                    { id: "prompt_template", label: "Template" },
                    { id: "rate_limit", label: "Rate Limits" },
                ].filter(tab => {
                    const isCompute = isComputeDeployment();

                    // Hide terminal for non-compute/external deployments
                    if (tab.id === "terminal" && !isCompute) return false;

                    // specific filtering
                    if (deployment?.workload_type === 'training') {
                        return ["overview", "logs", "terminal"].includes(tab.id)
                    }
                    return true
                }).map((tab) => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id as TabType)}
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

            {
                activeTab === "overview" && deployment && (
                    <DeploymentOverview deployment={deployment} />
                )
            }

            {
                activeTab === "logs" && id && (
                    <div className="bg-card rounded-xl border shadow-sm p-6">
                        <h3 className="text-lg font-medium mb-6">{deployment?.workload_type === 'training' ? 'Training Logs' : 'Inference Logs'}</h3>
                        {deployment?.workload_type === 'training' ? (
                            <TrainingLogs deploymentId={id} />
                        ) : (
                            <InferenceLogs deploymentId={id} />
                        )}
                    </div>
                )
            }

            {
                activeTab === "terminal" && id && (
                    <div className="space-y-4">
                        <TerminalLogs deploymentId={id} />
                    </div>
                )
            }

            {
                activeTab === "guardrail" && id && (
                    <DeploymentGuardrails deploymentId={id} />
                )
            }

            {
                activeTab === "rag" && id && (
                    <DeploymentRag deploymentId={id} />
                )
            }

            {
                activeTab === "prompt_template" && id && (
                    <DeploymentPromptTemplate deploymentId={id} />
                )
            }

            {
                activeTab === "rate_limit" && id && (
                    <DeploymentRateLimit deploymentId={id} />
                )
            }
        </div >
    )
}

