import { useState, useEffect } from "react"
import api from "@/lib/api"
import { ChevronDown, ChevronUp, Clock, Zap, Hash, User, AlertCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { Skeleton } from "@/components/ui/Skeleton"

interface InferenceLog {
    id: string
    deployment_id: string
    user_id: string
    model: string
    request_payload: Record<string, any> | null
    latency_ms: number | null
    ttft_ms: number | null
    tokens_per_second: number | null
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
    status_code: number
    error_message: string | null
    is_streaming: boolean
    applied_policies: string[] | null
    created_at: string
}

interface InferenceLogsProps {
    deploymentId: string
}

export default function InferenceLogs({ deploymentId }: InferenceLogsProps) {
    const [logs, setLogs] = useState<InferenceLog[]>([])
    const [loading, setLoading] = useState(true)
    const [expandedId, setExpandedId] = useState<string | null>(null)

    useEffect(() => {
        const fetchLogs = async () => {
            try {
                setLoading(true)
                const { data } = await api.get(`/management/deployments/${deploymentId}/logs`)
                setLogs(data)
            } catch (error) {
                console.error("Failed to fetch logs:", error)
            } finally {
                setLoading(false)
            }
        }

        if (deploymentId) {
            fetchLogs()
        }
    }, [deploymentId])

    const formatDate = (dateStr: string) => {
        return new Date(dateStr).toLocaleString()
    }

    const formatMs = (value: number | null) => {
        return value !== null ? `${value.toLocaleString()}ms` : "-"
    }

    const formatTokensPerSecond = (value: number | null) => {
        return value !== null
            ? `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })} tok/s`
            : "-"
    }

    const formatTokenBreakdown = (log: InferenceLog) => {
        return `${log.total_tokens.toLocaleString()} (${log.prompt_tokens.toLocaleString()}/${log.completion_tokens.toLocaleString()})`
    }

    const toggleExpand = (id: string) => {
        setExpandedId(expandedId === id ? null : id)
    }

    if (loading) {
        return (
            <div className="space-y-4">
                {[...Array(5)].map((_, i) => (
                    <div key={i} className="flex flex-col gap-2 p-4 border rounded-lg">
                        <div className="flex justify-between">
                            <Skeleton className="h-4 w-32" />
                            <div className="flex gap-4">
                                <Skeleton className="h-4 w-16" />
                                <Skeleton className="h-4 w-24" />
                            </div>
                        </div>
                        <Skeleton className="h-3 w-48" />
                    </div>
                ))}
            </div>
        )
    }

    if (logs.length === 0) {
        return (
            <div className="text-center py-12 text-muted-foreground">
                <Clock className="w-12 h-12 mx-auto mb-4 opacity-50" />
                <p>No inference logs yet</p>
                <p className="text-sm">Logs will appear here after API requests are made</p>
            </div>
        )
    }

    return (
        <div className="space-y-3">
            <div className="text-sm text-muted-foreground mb-4">
                Showing {logs.length} recent requests
            </div>

            {logs.map((log) => (
                <div
                    key={log.id}
                    className={cn(
                        "bg-card rounded-lg border shadow-sm overflow-hidden transition-all",
                        log.status_code >= 400 && "border-destructive/50"
                    )}
                >
                    {/* Main Row */}
                    <div
                        className="p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                        onClick={() => toggleExpand(log.id)}
                    >
                        <div className="flex items-center justify-between gap-4">
                            {/* Left: Time & Model */}
                            <div className="flex items-center gap-4 min-w-0 flex-1">
                                <div className="text-xs text-muted-foreground whitespace-nowrap">
                                    {formatDate(log.created_at)}
                                </div>
                                <div className="flex items-center gap-2 min-w-0">
                                    <span className="font-medium text-sm truncate max-w-[200px] md:max-w-none">{log.model}</span>
                                    {log.is_streaming && (
                                        <span className="px-1.5 py-0.5 text-[10px] bg-emerald-500/20 text-emerald-500 rounded shrink-0">
                                            STREAM
                                        </span>
                                    )}
                                    {log.status_code >= 400 && (
                                        <span className="px-1.5 py-0.5 text-[10px] bg-destructive/20 text-destructive rounded flex items-center gap-1 shrink-0">
                                            <AlertCircle className="w-3 h-3" /> {log.status_code}
                                        </span>
                                    )}
                                </div>
                            </div>

                            {/* Right: Stats */}
                            <div className="flex items-center gap-4 text-sm shrink-0 ml-auto">
                                {/* Total duration */}
                                <div className="flex items-center gap-1.5 text-muted-foreground" title="Total Duration">
                                    <Clock className="w-3.5 h-3.5" />
                                    <span className="font-mono">{formatMs(log.latency_ms)}</span>
                                </div>

                                {/* TTFT */}
                                <div className="hidden md:flex items-center gap-1.5 text-muted-foreground" title="TTFT (Time to First Token)">
                                    <span className="text-[10px] uppercase tracking-wide">TTFT</span>
                                    <span className="font-mono">{formatMs(log.ttft_ms)}</span>
                                </div>

                                {/* Speed */}
                                <div className="hidden lg:flex items-center gap-1.5 text-muted-foreground" title="Tokens/sec">
                                    <Zap className="w-3.5 h-3.5" />
                                    <span className="font-mono">
                                        {formatTokensPerSecond(log.tokens_per_second)}
                                    </span>
                                </div>

                                {/* Tokens */}
                                <div className="flex items-center gap-1.5 text-muted-foreground" title="Token Usage">
                                    <Hash className="w-3.5 h-3.5" />
                                    <span className="font-mono text-xs">
                                        {formatTokenBreakdown(log)}
                                    </span>
                                </div>

                                {/* Expand Icon */}
                                {expandedId === log.id ? (
                                    <ChevronUp className="w-4 h-4 text-muted-foreground" />
                                ) : (
                                    <ChevronDown className="w-4 h-4 text-muted-foreground" />
                                )}
                            </div>
                        </div>
                    </div>

                    {/* Expanded Details */}
                    {expandedId === log.id && (
                        <div className="border-t bg-muted/20 p-4 space-y-4">
                            {/* User & Request Info */}
                            <div className="grid grid-cols-2 md:grid-cols-5 gap-4 text-sm">
                                <div>
                                    <div className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
                                        <User className="w-3 h-3" /> User ID
                                    </div>
                                    <div className="font-mono text-xs truncate" title={log.user_id}>
                                        {log.user_id}
                                    </div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground mb-1">Total Duration</div>
                                    <div className="font-medium">{formatMs(log.latency_ms)}</div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground mb-1">TTFT</div>
                                    <div className="font-medium">{formatMs(log.ttft_ms)}</div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground mb-1">Speed</div>
                                    <div className="font-medium">
                                        {formatTokensPerSecond(log.tokens_per_second)}
                                    </div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground mb-1">Token Usage</div>
                                    <div className="font-medium">
                                        {formatTokenBreakdown(log)}
                                    </div>
                                </div>
                            </div>

                            {/* Applied Policies */}
                            {log.applied_policies && log.applied_policies.length > 0 && (
                                <div>
                                    <div className="text-xs text-muted-foreground mb-2 flex items-center gap-1">
                                        <Zap className="w-3 h-3 text-yellow-500" /> Applied Policies
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                        {log.applied_policies.map((policy) => (
                                            <span
                                                key={policy}
                                                className="px-2 py-0.5 text-[10px] font-mono bg-primary/10 text-primary border border-primary/20 rounded uppercase"
                                            >
                                                {policy.replace('_', ' ')}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}

                            {/* Error Message */}
                            {log.error_message && (
                                <div className="p-3 bg-destructive/10 border border-destructive/30 rounded-md">
                                    <div className="text-xs text-destructive font-medium mb-1">Error</div>
                                    <div className="text-sm text-destructive">{log.error_message}</div>
                                </div>
                            )}

                            {/* Request Payload */}
                            <div className="min-w-0 flex-1">
                                <div className="text-xs text-muted-foreground mb-2 font-medium uppercase tracking-tight">Request Payload</div>
                                {log.request_payload ? (
                                    <pre className="p-3 bg-slate-950 text-slate-300 rounded-md text-xs overflow-x-auto max-h-96 overflow-y-auto whitespace-pre-wrap break-all scrollbar-thin scrollbar-thumb-slate-700">
                                        {JSON.stringify(log.request_payload, null, 2)}
                                    </pre>
                                ) : (
                                    <div className="p-3 bg-muted/50 rounded-md text-xs italic text-muted-foreground border border-dashed text-center">
                                        Payload logging is disabled for this organization.
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            ))}
        </div>
    )
}
