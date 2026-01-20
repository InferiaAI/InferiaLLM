import { useEffect, useState } from "react"
import { useParams, Link, useNavigate } from "react-router-dom"
import api from "@/lib/api"
import { Activity, Gauge, Trash2 } from "lucide-react"
import { cn } from "@/lib/utils"
import InferenceLogs from "@/components/InferenceLogs"
import TrainingLogs from "@/components/deployment/TrainingLogs"
import DeploymentOverview from "@/components/deployment/DeploymentOverview"
import DeploymentGuardrails from "@/components/deployment/DeploymentGuardrails"
import DeploymentRag from "@/components/deployment/DeploymentRag"
import DeploymentPromptTemplate from "@/components/deployment/DeploymentPromptTemplate"
import DeploymentRateLimit from "@/components/deployment/DeploymentRateLimit"
import { toast } from "sonner"

type TabType = "overview" | "logs" | "guardrail" | "rag" | "prompt_template" | "rate_limit"

import { LoadingScreen } from "@/components/ui/LoadingScreen"



export default function DeploymentDetail() {
    const { id } = useParams<{ id: string }>()
    const navigate = useNavigate()
    const [activeTab, setActiveTab] = useState<TabType>("overview")
    const [loading, setLoading] = useState(true)
    const [deleting, setDeleting] = useState(false)

    // Deployment Info
    const [deployment, setDeployment] = useState<any>(null)

    const fetchDeployment = async () => {
        setLoading(true)
        try {
            // 1. Try fetching from Compute Orchestrator directly
            try {
                const { data } = await api.get(`http://localhost:8080/deployment/status/${id}`)
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
            const { data } = await api.get("/management/deployments")
            const found = data.find((d: any) => d.id === id)
            setDeployment(found)
        } catch (e) {
            console.error(e)
        } finally {
            setLoading(false)
        }
    }

    const handleDelete = async () => {
        if (!id) return

        const isStopped = ["STOPPED", "TERMINATED", "FAILED"].includes(deployment?.state || deployment?.status || "")
        const confirmMsg = isStopped
            ? "Are you sure you want to permanently delete this deployment? This action cannot be undone."
            : "Are you sure you want to stop this deployment?"

        if (!confirm(confirmMsg)) {
            return
        }

        setDeleting(true)
        try {
            // Determine provider type for appropriate API call
            const isCompute = deployment?.provider?.includes("Compute") || deployment?.engine === "vllm"

            if (isCompute) {
                if (isStopped) {
                    // Permanently delete from database
                    await api.delete(`http://localhost:8080/deployment/delete/${id}`)
                } else {
                    // Stop the deployment first
                    await api.post("http://localhost:8080/deployment/terminate", {
                        deployment_id: id,
                    })
                }
            } else {
                await api.delete(`/management/deployments/${id}`)
            }

            toast.success(isStopped ? "Deployment deleted successfully" : "Deployment stopped successfully")
            if (isStopped) {
                navigate("/dashboard/deployments")
            } else {
                // Refresh to show updated state
                fetchDeployment()
            }
        } catch (err: any) {
            toast.error(err.response?.data?.detail || "Failed to delete deployment")
        } finally {
            setDeleting(false)
        }
    }

    useEffect(() => {
        if (id) fetchDeployment()
    }, [id])


    if (loading) return <LoadingScreen message="Loading deployment details..." />
    if (!deployment && !id) return <div>Deployment not found</div>

    return (
        <div className="space-y-6">
            {/* Breadcrumb-ish Header */}
            <div className="flex items-center text-sm text-muted-foreground mb-2">
                <Link to="/management/deployments" className="hover:text-foreground">Deployments</Link>
                <span className="mx-2">/</span>
                <span className="text-foreground font-medium">{deployment?.provider || "..."}</span>
                <span className="mx-2">/</span>
                <span>{deployment?.model_name || id}</span>
            </div>

            {/* Title & Actions */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight mb-2">{deployment?.model_name || "Deployment"}</h1>
                    <div className="flex items-center gap-2">
                        <span className="text-muted-foreground text-sm">Provider:</span>
                        <span className="px-2 py-0.5 rounded-md bg-muted text-xs font-medium uppercase tracking-wider">{deployment?.provider || "Unknown"}</span>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={fetchDeployment}
                        className="px-3 py-1.5 bg-background border rounded-md text-sm font-medium hover:bg-muted flex items-center gap-2 transition-colors"
                    >
                        <Activity className="w-4 h-4" /> Refresh
                    </button>
                    <button className="px-3 py-1.5 bg-background border rounded-md text-sm font-medium hover:bg-muted flex items-center gap-2 transition-colors">
                        <Gauge className="w-4 h-4" /> Metrics
                    </button>
                    <button
                        onClick={handleDelete}
                        disabled={deleting}
                        className="px-3 py-1.5 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800 rounded-md text-sm font-medium hover:bg-red-100 dark:hover:bg-red-900/40 flex items-center gap-2 transition-colors disabled:opacity-50"
                    >
                        <Trash2 className="w-4 h-4" /> {deleting ? "Deleting..." : "Delete"}
                    </button>
                </div>
            </div>

            {/* Tabs */}
            <div className="flex gap-1 border-b pb-0">
                {[
                    { id: "overview", label: "Overview" },
                    { id: "guardrail", label: "Guardrails" },
                    { id: "rag", label: "RAG & Data" },
                    { id: "logs", label: "Logs" },
                    { id: "prompt_template", label: "Template" },
                    { id: "rate_limit", label: "Rate Limits" },
                ].filter(tab => {
                    // specific filtering
                    if (deployment?.workload_type === 'training') {
                        return ["overview", "logs"].includes(tab.id)
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

            {activeTab === "overview" && deployment && (
                <DeploymentOverview deployment={deployment} />
            )}

            {activeTab === "logs" && id && (
                <div className="bg-card rounded-xl border shadow-sm p-6">
                    <h3 className="text-lg font-medium mb-6">{deployment?.workload_type === 'training' ? 'Training Logs' : 'Inference Logs'}</h3>
                    {deployment?.workload_type === 'training' ? (
                        <TrainingLogs deploymentId={id} />
                    ) : (
                        <InferenceLogs deploymentId={id} />
                    )}
                </div>
            )}

            {activeTab === "guardrail" && id && (
                <DeploymentGuardrails deploymentId={id} />
            )}

            {activeTab === "rag" && id && (
                <DeploymentRag deploymentId={id} />
            )}

            {activeTab === "prompt_template" && id && (
                <DeploymentPromptTemplate deploymentId={id} />
            )}

            {activeTab === "rate_limit" && id && (
                <DeploymentRateLimit deploymentId={id} />
            )}
        </div>
    )
}

