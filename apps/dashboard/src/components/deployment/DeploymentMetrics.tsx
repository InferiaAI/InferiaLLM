import { useQuery } from "@tanstack/react-query"
import { computeApi } from "@/lib/api"
import { Cpu, Activity, Gauge, Clock, Database, Zap, BarChart3 } from "lucide-react"

interface DeploymentData {
    id?: string
    deployment_id?: string
    target_node_id?: string
    node_ids?: string[]
    engine?: string
    [key: string]: any
}

interface DeploymentMetricsProps {
    deploymentId: string
    deployment: DeploymentData
}

interface NodeMetrics {
    gpu_allocated?: number
    gpu_total?: number
    vcpu_allocated?: number
    vcpu_total?: number
    ram_gb_allocated?: number
    ram_gb_total?: number
    cpu_usage_pct?: number
    mem_usage_gb?: number
    health_score?: number
}

interface DeployMetric {
    recipe?: string
    model?: string
    requests_total?: number
    active_requests?: number
    request_latency_p50_ms?: number
    request_latency_p95_ms?: number
    pull_duration_ms?: number
    start_duration_ms?: number
    phase?: string
    engine_metrics?: Record<string, number>
}

export default function DeploymentMetrics({ deploymentId, deployment }: DeploymentMetricsProps) {
    const targetNodeId = deployment.target_node_id || deployment.node_ids?.[0]

    const { data: nodeData } = useQuery({
        queryKey: ["node", targetNodeId],
        queryFn: async () => {
            const { data } = await computeApi.get<NodeMetrics>(`/nodes/${targetNodeId}`)
            return data
        },
        enabled: !!targetNodeId,
        refetchInterval: 30_000,
    })

    const { data: deployMetric } = useQuery({
        queryKey: ["deploy-metrics", targetNodeId, deploymentId],
        queryFn: async () => {
            const { data } = await computeApi.get<DeployMetric>(`/nodes/${targetNodeId}/deploy-metrics/${deploymentId}`)
            return data
        },
        enabled: !!targetNodeId,
        refetchInterval: 30_000,
    })

    if (!targetNodeId) {
        return (
            <div className="bg-card rounded-xl border shadow-sm p-6">
                <p className="text-sm text-muted-foreground">No node assigned to this deployment yet.</p>
            </div>
        )
    }

    return (
        <div className="space-y-4">
            {nodeData && <NodeMetricsCards metrics={nodeData} />}
            {deployMetric ? <DeployMetricCard metric={deployMetric} engine={deployment.engine} /> : <EmptyMetrics />}
        </div>
    )
}

function NodeMetricsCards({ metrics }: { metrics: NodeMetrics }) {
    const items = [
        {
            label: "GPU",
            value: `${metrics.gpu_allocated ?? "?"}/${metrics.gpu_total ?? "?"}`,
            icon: Cpu,
            color: "text-blue-600 dark:text-blue-400",
            bg: "bg-blue-50 dark:bg-blue-950/30",
        },
        {
            label: "CPU Usage",
            value: metrics.cpu_usage_pct != null ? `${metrics.cpu_usage_pct.toFixed(1)}%` : `${metrics.vcpu_allocated ?? "?"}/${metrics.vcpu_total ?? "?"}`,
            icon: Zap,
            color: "text-purple-600 dark:text-purple-400",
            bg: "bg-purple-50 dark:bg-purple-950/30",
        },
        {
            label: "RAM Usage",
            value: metrics.mem_usage_gb != null ? `${metrics.mem_usage_gb.toFixed(1)} / ${metrics.ram_gb_total ?? "?"} GB` : `${metrics.ram_gb_allocated ?? "?"}/${metrics.ram_gb_total ?? "?"} GB`,
            icon: Database,
            color: "text-green-600 dark:text-green-400",
            bg: "bg-green-50 dark:bg-green-950/30",
        },
        {
            label: "Health Score",
            value: metrics.health_score != null ? `${metrics.health_score}/100` : "N/A",
            icon: Activity,
            color: metrics.health_score != null && metrics.health_score >= 80
                ? "text-green-600 dark:text-green-400"
                : metrics.health_score != null && metrics.health_score >= 50
                    ? "text-amber-600 dark:text-amber-400"
                    : "text-red-600 dark:text-red-400",
            bg: metrics.health_score != null && metrics.health_score >= 80
                ? "bg-green-50 dark:bg-green-950/30"
                : metrics.health_score != null && metrics.health_score >= 50
                    ? "bg-amber-50 dark:bg-amber-950/30"
                    : "bg-red-50 dark:bg-red-950/30",
        },
    ]

    return (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {items.map((item) => (
                <div key={item.label} className="rounded-xl border shadow-sm p-4 bg-card">
                    <div className="flex items-center gap-3">
                        <div className={`p-2 rounded-lg ${item.bg}`}>
                            <item.icon className={`w-5 h-5 ${item.color}`} />
                        </div>
                        <div>
                            <p className="text-xs text-muted-foreground">{item.label}</p>
                            <p className="text-lg font-semibold">{item.value}</p>
                        </div>
                    </div>
                </div>
            ))}
        </div>
    )
}

function DeployMetricCard({ metric, engine }: { metric: DeployMetric; engine?: string }) {
    const items = [
        { label: "Recipe", value: metric.recipe || "—", icon: BarChart3 },
        { label: "Phase", value: metric.phase || "—", icon: Activity },
        { label: "Requests (sliding)", value: metric.requests_total ?? "—", icon: Gauge },
        { label: "Active Requests", value: metric.active_requests ?? "—", icon: Gauge },
        { label: "Latency P95", value: metric.request_latency_p50_ms != null && metric.request_latency_p50_ms > 0 ? `${metric.request_latency_p50_ms} ms` : "—", icon: Clock },
        { label: "Latency P98", value: metric.request_latency_p95_ms != null && metric.request_latency_p95_ms > 0 ? `${metric.request_latency_p95_ms} ms` : "—", icon: Clock },
        { label: "Pull Duration", value: metric.pull_duration_ms != null ? `${(metric.pull_duration_ms / 1000).toFixed(1)}s` : "—", icon: Clock },
        { label: "Start Duration", value: metric.start_duration_ms != null ? `${(metric.start_duration_ms / 1000).toFixed(1)}s` : "—", icon: Clock },
    ]

    return (
        <div className="bg-card rounded-xl border shadow-sm p-6 space-y-4">
            <h3 className="text-lg font-medium flex items-center gap-2">
                <Gauge className="w-5 h-5" />
                Per-Deployment Metrics
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                {items.map((item) => (
                    <div key={item.label} className="space-y-1">
                        <p className="text-xs text-muted-foreground flex items-center gap-1.5">
                            <item.icon className="w-3 h-3" />
                            {item.label}
                        </p>
                        <p className="text-sm font-semibold">{item.value}</p>
                    </div>
                ))}
            </div>
            {engine === "vllm" && metric.engine_metrics && Object.keys(metric.engine_metrics).length > 0 && (
                <>
                    <hr className="border-border" />
                    <div>
                        <h4 className="text-sm font-medium text-muted-foreground mb-3">Engine Metrics (vLLM)</h4>
                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                            {Object.entries(metric.engine_metrics).map(([key, val]) => (
                                <div key={key} className="space-y-1">
                                    <p className="text-xs text-muted-foreground font-mono">{key.replace(/^vllm:/, "")}</p>
                                    <p className="text-sm font-semibold">{val}</p>
                                </div>
                            ))}
                        </div>
                    </div>
                </>
            )}
        </div>
    )
}

function EmptyMetrics() {
    return (
        <div className="bg-card rounded-xl border shadow-sm p-6">
            <div className="flex items-center gap-3 text-muted-foreground">
                <BarChart3 className="w-5 h-5" />
                <div>
                    <p className="text-sm font-medium">No runtime metrics available</p>
                    <p className="text-xs">Metrics will appear once the inference engine serves requests. This deployment may use ollama, which does not expose engine-level metrics.</p>
                </div>
            </div>
        </div>
    )
}
