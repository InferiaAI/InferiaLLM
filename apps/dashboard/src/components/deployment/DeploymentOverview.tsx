import { CheckCircle2, Loader2, AlertCircle, StopCircle, Eye, EyeOff, Copy, Database, Cpu, Clock, Activity, Gauge } from "lucide-react"
import { toast } from "sonner"
import { cn } from "@/lib/utils"
import { useState } from "react"
import { INFERENCE_URL, api } from "@/lib/api"
import { useQuery } from "@tanstack/react-query"

interface DeploymentData {
    id?: string
    deployment_id?: string
    model_type?: string
    engine?: string
    state?: string
    status?: string
    workload_type?: string
    endpoint_url?: string
    gpu_per_replica?: number
    created_at?: string
    model_name?: string
    provider?: string
    git_repo?: string
    training_script?: string
}

interface DeploymentOverviewProps {
    deployment: DeploymentData
}

interface DeploymentLog {
    created_at: string
    latency_ms: number | null
    tokens_per_second: number | null
    status_code: number
}

function formatMetricNumber(value: number | null, suffix = "") {
    if (value === null || Number.isNaN(value)) return "-"
    return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}${suffix}`
}

function calculateP95(values: number[]): number | null {
    if (values.length === 0) return null
    const sorted = [...values].sort((a, b) => a - b)
    const index = Math.ceil(0.95 * sorted.length) - 1
    return sorted[Math.max(0, index)]
}

export default function DeploymentOverview({ deployment }: DeploymentOverviewProps) {
    const [showRawEndpoint, setShowRawEndpoint] = useState(false)
    const inferenceBaseUrl = INFERENCE_URL.replace(/\/$/, "")
    const publicInferenceEndpoint = `${inferenceBaseUrl}/v1/chat/completions`
    const publicEmbeddingEndpoint = `${inferenceBaseUrl}/v1/embeddings`

    const deploymentId = deployment?.id || deployment?.deployment_id

    const isEmbedding = deployment?.model_type === "embedding" || deployment?.engine === "infinity" || deployment?.engine === "tei"
    const state = deployment?.state || deployment?.status || "Unknown"

    const { data: embeddingMetrics, isLoading: loadingLogMetrics } = useQuery({
        queryKey: ["deployment-overview-metrics", deploymentId],
        enabled: !!deploymentId && isEmbedding,
        queryFn: async () => {
            const { data } = await api.get<DeploymentLog[]>(`/management/deployments/${deploymentId}/logs`)
            const logs = data || []

            if (logs.length === 0) {
                return {
                    requestCount: 0,
                    avgLatency: null as number | null,
                    p95Latency: null as number | null,
                    avgTokensPerSecond: null as number | null,
                }
            }

            const latencies = logs
                .map((log) => log.latency_ms)
                .filter((value): value is number => value !== null && value >= 0)

            const tokensPerSecond = logs
                .map((log) => log.tokens_per_second)
                .filter((value): value is number => value !== null && value >= 0)

            const avgLatency = latencies.length > 0 ? latencies.reduce((sum, value) => sum + value, 0) / latencies.length : null
            const p95Latency = calculateP95(latencies)
            const avgTokensPerSecond = tokensPerSecond.length > 0 ? tokensPerSecond.reduce((sum, value) => sum + value, 0) / tokensPerSecond.length : null

            return {
                requestCount: logs.length,
                avgLatency,
                p95Latency,
                avgTokensPerSecond,
            }
        },
        staleTime: 60 * 1000,
    })

    if (!deployment) return null

    return (
        <div className="space-y-6">
            <div className="bg-card rounded-lg border shadow-sm p-6">
                <div className="flex items-center justify-between mb-6">
                    <h3 className="font-mono text-sm font-bold uppercase tracking-wider">Status Overview</h3>
                    {isEmbedding && (
                        <span className="flex items-center gap-2 px-3 py-1 rounded-full bg-purple-100 text-purple-700 text-xs font-medium border border-purple-200">
                            <Database className="w-3 h-3" />
                            Embedding Model
                        </span>
                    )}
                </div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-8">
                    <div>
                        <div className="text-xs text-muted-foreground font-mono mb-2 uppercase">Status</div>
                        <div className={cn(
                            "flex items-center gap-2 font-medium",
                            (state === "READY" || state === "RUNNING") ? "text-green-600 dark:text-green-500" :
                                state === "STOPPED" ? "text-slate-500" :
                                    state === "FAILED" ? "text-red-600" :
                                        "text-yellow-600"
                        )}>
                            {(state === "READY" || state === "RUNNING") ? <CheckCircle2 className="w-5 h-5" /> :
                                state === "STOPPED" || state === "TERMINATED" ? <StopCircle className="w-5 h-5" /> :
                                    state === "FAILED" ? <AlertCircle className="w-5 h-5" /> :
                                        <Loader2 className="w-5 h-5 animate-spin" />}
                            {state}
                        </div>
                    </div>
                    <div>
                        <div className="text-xs text-muted-foreground font-mono mb-2 uppercase">Ready Replicas</div>
                        <div className="text-lg font-medium">
                            {(state === "READY" || state === "RUNNING") ? "1 / 1" : "0 / 1"}
                        </div>
                    </div>
                    <div>
                        <div className="text-xs text-muted-foreground font-mono mb-2 uppercase">Updated Replicas</div>
                        <div className="text-lg font-medium">1</div>
                    </div>
                    <div>
                        <div className="text-xs text-muted-foreground font-mono mb-2 uppercase">Available Replicas</div>
                        <div className="text-lg font-medium">
                            {(state === "READY" || state === "RUNNING") ? "1" : "0"}
                        </div>
                    </div>
                </div>
            </div>

            {isEmbedding && (
                <div className="bg-card rounded-lg border shadow-sm p-6">
                    <div className="flex items-center justify-between mb-6">
                        <h3 className="font-mono text-sm font-bold uppercase tracking-wider">Embedding Metrics Snapshot</h3>
                    </div>

                    {loadingLogMetrics ? (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <Loader2 className="w-4 h-4 animate-spin" />
                            Loading metrics...
                        </div>
                    ) : (
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <div className="rounded-lg border p-4 bg-muted/20">
                                <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1 inline-flex items-center gap-1">
                                    <Activity className="w-3.5 h-3.5" /> Recent Requests
                                </div>
                                <div className="text-xl font-semibold">{embeddingMetrics?.requestCount ?? 0}</div>
                            </div>
                            <div className="rounded-lg border p-4 bg-muted/20">
                                <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1 inline-flex items-center gap-1">
                                    <Clock className="w-3.5 h-3.5" /> Avg Latency
                                </div>
                                <div className="text-xl font-semibold">{formatMetricNumber(embeddingMetrics?.avgLatency ?? null, " ms")}</div>
                            </div>
                            <div className="rounded-lg border p-4 bg-muted/20">
                                <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1 inline-flex items-center gap-1">
                                    <Gauge className="w-3.5 h-3.5" /> P95 Latency
                                </div>
                                <div className="text-xl font-semibold">{formatMetricNumber(embeddingMetrics?.p95Latency ?? null, " ms")}</div>
                            </div>
                            <div className="rounded-lg border p-4 bg-muted/20">
                                <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1 inline-flex items-center gap-1">
                                    <Cpu className="w-3.5 h-3.5" /> Avg Throughput
                                </div>
                                <div className="text-xl font-semibold">{formatMetricNumber(embeddingMetrics?.avgTokensPerSecond ?? null, " tok/s")}</div>
                            </div>
                        </div>
                    )}
                </div>
            )}

            <div className="bg-card rounded-lg border shadow-sm p-6">
                <h3 className="font-mono text-sm font-bold uppercase tracking-wider mb-6">Deployment Information</h3>
                <div className="grid md:grid-cols-2 gap-8">
                    <div className="space-y-6">
                        <div>
                            <div className="text-xs text-muted-foreground font-mono mb-1">Created</div>
                            <div className="font-mono text-sm">{new Date(deployment.created_at).toLocaleString()}</div>
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground font-mono mb-1">Replicas</div>
                            <div className="font-mono text-sm">1</div>
                        </div>
                    </div>
                    <div className="space-y-6">
                        <div>
                            <div className="text-xs text-muted-foreground font-mono mb-1">Strategy</div>
                            <div className="font-mono text-sm">RollingUpdate</div>
                        </div>
                        <div>
                            <div className="text-xs text-muted-foreground font-mono mb-1">Selector</div>
                            <div className="flex gap-2">
                                <span className="bg-muted px-2 py-0.5 rounded text-xs font-mono border">app: {deployment.model_name}</span>
                                <span className="bg-muted px-2 py-0.5 rounded text-xs font-mono border">provider: {deployment.provider}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <div className="mt-8 pt-8 border-t">
                    {deployment.workload_type === "training" ? (
                        <>
                            <div className="flex items-center justify-between mb-4">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase tracking-wider">Training Monitor</div>
                                    <p className="text-sm text-muted-foreground">Access TensorBoard or training logs directly.</p>
                                </div>
                                {deployment.endpoint_url && (
                                    <span className="px-2 py-1 rounded bg-green-100 text-green-700 text-xs font-mono border border-green-200">Active</span>
                                )}
                            </div>
                            <div className="bg-muted p-4 rounded-lg border font-mono text-sm flex justify-between items-center">
                                <div className="flex flex-col gap-1">
                                    <span className="text-muted-foreground text-xs uppercase">TensorBoard URL</span>
                                    {deployment.endpoint_url ? (
                                        <a href={deployment.endpoint_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline break-all">
                                            {deployment.endpoint_url}
                                        </a>
                                    ) : (
                                        <div className="flex items-center gap-2 text-muted-foreground">
                                            <Loader2 className="w-3 h-3 animate-spin" />
                                            <span className="text-xs italic">Waiting for TensorBoard...</span>
                                        </div>
                                    )}
                                </div>
                                {deployment.endpoint_url && (
                                    <a
                                        href={deployment.endpoint_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="ml-4 px-4 py-2 bg-blue-600 text-white rounded-md text-xs font-medium hover:bg-blue-700 transition-colors shrink-0"
                                    >
                                        Open Board
                                    </a>
                                )}
                            </div>

                            <div className="mt-6 grid md:grid-cols-2 gap-6">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase">Git Repository</div>
                                    <div className="p-3 bg-muted rounded border text-sm font-mono break-all">{deployment.git_repo || "N/A"}</div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase">Training Command</div>
                                    <div className="p-3 bg-muted rounded border text-sm font-mono break-all overflow-x-auto whitespace-pre-wrap">{deployment.training_script || "N/A"}</div>
                                </div>
                            </div>
                        </>
                    ) : isEmbedding ? (
                        <>
                            <div className="flex items-center justify-between mb-4">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase tracking-wider">Raw Embedding Endpoint</div>
                                    <p className="text-sm text-muted-foreground">The direct URL to the embedding service (hidden by default).</p>
                                </div>
                            </div>
                            <div className="bg-muted p-4 rounded-lg border font-mono text-sm flex justify-between items-center group relative overflow-hidden mb-6">
                                <div className="flex flex-col gap-1 w-full">
                                    {deployment.endpoint_url ? (
                                        <div className="flex items-center gap-2">
                                            <span className="text-primary break-all">{showRawEndpoint ? deployment.endpoint_url : "•".repeat(deployment.endpoint_url.length)}</span>
                                        </div>
                                    ) : (
                                        <div className="flex items-center gap-2 text-muted-foreground">
                                            <Loader2 className="w-3 h-3 animate-spin" />
                                            <span className="text-xs italic">Fetching endpoint...</span>
                                        </div>
                                    )}
                                </div>
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={() => setShowRawEndpoint(!showRawEndpoint)}
                                        className="p-2 hover:bg-slate-200 dark:hover:bg-slate-800 rounded-md transition-colors text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 shrink-0"
                                        title={showRawEndpoint ? "Hide Endpoint" : "Show Endpoint"}
                                        disabled={!deployment.endpoint_url}
                                    >
                                        {showRawEndpoint ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                    <button
                                        onClick={() => {
                                            if (deployment.endpoint_url) {
                                                navigator.clipboard.writeText(deployment.endpoint_url)
                                                toast.success("Raw endpoint copied to clipboard")
                                            }
                                        }}
                                        className="p-2 hover:bg-slate-200 dark:hover:bg-slate-800 rounded-md transition-colors text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 shrink-0"
                                        title="Copy Endpoint"
                                        disabled={!deployment.endpoint_url}
                                    >
                                        <Copy className="w-4 h-4" />
                                    </button>
                                </div>
                            </div>

                            <div className="flex items-center justify-between mb-4">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase tracking-wider">Public Embedding API</div>
                                    <p className="text-sm text-muted-foreground">OpenAI-compatible embeddings endpoint.</p>
                                </div>
                                {deployment.endpoint_url && (
                                    <span className="px-2 py-1 rounded bg-green-100 text-green-700 text-xs font-mono border border-green-200">Active</span>
                                )}
                            </div>
                            <div className="bg-muted p-4 rounded-lg border font-mono text-sm flex justify-between items-center group relative overflow-hidden">
                                <div className="flex flex-col gap-1 w-full">
                                    <span className="text-primary break-all">{publicEmbeddingEndpoint}</span>
                                </div>
                                <button
                                    onClick={() => {
                                        navigator.clipboard.writeText(publicEmbeddingEndpoint)
                                        toast.success("Endpoint copied to clipboard")
                                    }}
                                    className="ml-4 p-2 hover:bg-slate-200 dark:hover:bg-slate-800 rounded-md transition-colors text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 shrink-0"
                                    title="Copy Endpoint"
                                >
                                    <Copy className="w-4 h-4" />
                                </button>
                            </div>

                            <div className="mt-6 grid md:grid-cols-2 gap-6">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase">Embedding Engine</div>
                                    <div className="flex items-center gap-2 p-3 bg-muted rounded border">
                                        <Cpu className="w-4 h-4 text-purple-500" />
                                        <span className="font-mono text-sm">{deployment.engine || "infinity"}</span>
                                    </div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase">Model Type</div>
                                    <div className="flex items-center gap-2 p-3 bg-muted rounded border">
                                        <Database className="w-4 h-4 text-purple-500" />
                                        <span className="font-mono text-sm">Embedding ({deployment.gpu_per_replica && deployment.gpu_per_replica > 0 ? "GPU" : "CPU"})</span>
                                    </div>
                                </div>
                            </div>
                        </>
                    ) : (
                        <>
                            <div className="flex items-center justify-between mb-4">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase tracking-wider">Raw Inference Endpoint</div>
                                    <p className="text-sm text-muted-foreground">The direct URL to the inference service (hidden by default).</p>
                                </div>
                            </div>
                            <div className="bg-muted p-4 rounded-lg border font-mono text-sm flex justify-between items-center group relative overflow-hidden mb-6">
                                <div className="flex flex-col gap-1 w-full">
                                    {deployment.endpoint_url ? (
                                        <div className="flex items-center gap-2">
                                            <span className="text-primary break-all">{showRawEndpoint ? deployment.endpoint_url : "•".repeat(deployment.endpoint_url.length)}</span>
                                        </div>
                                    ) : (
                                        <div className="flex items-center gap-2 text-muted-foreground">
                                            <Loader2 className="w-3 h-3 animate-spin" />
                                            <span className="text-xs italic">Fetching endpoint...</span>
                                        </div>
                                    )}
                                </div>
                                <div className="flex items-center gap-2">
                                    <button
                                        onClick={() => setShowRawEndpoint(!showRawEndpoint)}
                                        className="p-2 hover:bg-slate-200 dark:hover:bg-slate-800 rounded-md transition-colors text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 shrink-0"
                                        title={showRawEndpoint ? "Hide Endpoint" : "Show Endpoint"}
                                        disabled={!deployment.endpoint_url}
                                    >
                                        {showRawEndpoint ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                    <button
                                        onClick={() => {
                                            if (deployment.endpoint_url) {
                                                navigator.clipboard.writeText(deployment.endpoint_url)
                                                toast.success("Raw endpoint copied to clipboard")
                                            }
                                        }}
                                        className="p-2 hover:bg-slate-200 dark:hover:bg-slate-800 rounded-md transition-colors text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 shrink-0"
                                        title="Copy Endpoint"
                                        disabled={!deployment.endpoint_url}
                                    >
                                        <Copy className="w-4 h-4" />
                                    </button>
                                </div>
                            </div>

                            <div className="flex items-center justify-between mb-4">
                                <div>
                                    <div className="text-xs text-muted-foreground font-mono mb-1 uppercase tracking-wider">Inference Endpoint</div>
                                    <p className="text-sm text-muted-foreground">The public endpoint for chat completions.</p>
                                </div>
                                {deployment.endpoint_url && (
                                    <span className="px-2 py-1 rounded bg-green-100 text-green-700 text-xs font-mono border border-green-200">Active</span>
                                )}
                            </div>
                            <div className="bg-muted p-4 rounded-lg border font-mono text-sm flex justify-between items-center group relative overflow-hidden">
                                <div className="flex flex-col gap-1 w-full">
                                    <span className="text-primary break-all">{publicInferenceEndpoint}</span>
                                </div>
                                <button
                                    onClick={() => {
                                        navigator.clipboard.writeText(publicInferenceEndpoint)
                                        toast.success("Endpoint copied to clipboard")
                                    }}
                                    className="ml-4 p-2 hover:bg-slate-200 dark:hover:bg-slate-800 rounded-md transition-colors text-slate-400 hover:text-slate-900 dark:hover:text-slate-100 shrink-0"
                                    title="Copy Endpoint"
                                >
                                    <Copy className="w-4 h-4" />
                                </button>
                            </div>
                        </>
                    )}
                </div>
            </div>
        </div>
    )
}
