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
                            <div className="flex items-center gap-4 min-w-0">
                                <div className="text-xs text-muted-foreground whitespace-nowrap">
                                    {formatDate(log.created_at)}
                                </div>
                                <div className="flex items-center gap-2">
                                    <span className="font-medium text-sm truncate">{log.model}</span>
                                    {log.is_streaming && (
                                        <span className="px-1.5 py-0.5 text-[10px] bg-blue-500/20 text-blue-500 rounded">
                                            STREAM
                                        </span>
                                    )}
                                    {log.status_code >= 400 && (
                                        <span className="px-1.5 py-0.5 text-[10px] bg-destructive/20 text-destructive rounded flex items-center gap-1">
                                            <AlertCircle className="w-3 h-3" /> {log.status_code}
                                        </span>
                                    )}
                                </div>
                            </div>

                            {/* Right: Stats */}
                            <div className="flex items-center gap-6 text-sm">
                                {/* Latency */}
                                <div className="flex items-center gap-1.5 text-muted-foreground" title="Latency">
                                    <Clock className="w-3.5 h-3.5" />
                                    <span className="font-mono">{log.latency_ms ? `${log.latency_ms}ms` : "-"}</span>
                                </div>

                                {/* Speed */}
                                <div className="flex items-center gap-1.5 text-muted-foreground" title="Tokens/sec">
                                    <Zap className="w-3.5 h-3.5" />
                                    <span className="font-mono">
                                        {log.tokens_per_second ? `${log.tokens_per_second} tok/s` : "-"}
                                    </span>
                                </div>

                                {/* Tokens */}
                                <div className="flex items-center gap-1.5 text-muted-foreground" title="Token Usage">
                                    <Hash className="w-3.5 h-3.5" />
                                    <span className="font-mono text-xs">
                                        <span className="text-green-500">{log.prompt_tokens}</span>
                                        <span className="mx-1">/</span>
                                        <span className="text-blue-500">{log.completion_tokens}</span>
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
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                                <div>
                                    <div className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
                                        <User className="w-3 h-3" /> User ID
                                    </div>
                                    <div className="font-mono text-xs truncate" title={log.user_id}>
                                        {log.user_id}
                                    </div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground mb-1">Latency</div>
                                    <div className="font-medium">{log.latency_ms ? `${log.latency_ms}ms` : "N/A"}</div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground mb-1">Speed</div>
                                    <div className="font-medium">
                                        {log.tokens_per_second ? `${log.tokens_per_second} tokens/sec` : "N/A"}
                                    </div>
                                </div>
                                <div>
                                    <div className="text-xs text-muted-foreground mb-1">Token Usage</div>
                                    <div className="font-medium">
                                        Input: {log.prompt_tokens} | Output: {log.completion_tokens}
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
                            <div>
                                <div className="text-xs text-muted-foreground mb-2">Request Payload</div>
                                {log.request_payload ? (
                                    <pre className="p-3 bg-muted rounded-md text-xs overflow-x-auto max-h-64 overflow-y-auto">
                                        {JSON.stringify(log.request_payload, null, 2)}
                                    </pre>
                                ) : (
                                    <div className="p-3 bg-muted/50 rounded-md text-xs italic text-muted-foreground">
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
