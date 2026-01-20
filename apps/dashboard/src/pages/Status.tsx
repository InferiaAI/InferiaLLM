import { useQuery } from "@tanstack/react-query"
import { Activity, Server, Database, Zap, Cloud, Check, X, RefreshCw, Clock, AlertTriangle } from "lucide-react"
import { cn } from "@/lib/utils"
import { useState } from "react"

interface ServiceStatus {
    name: string
    url: string
    status: "online" | "offline" | "unknown"
    latency?: number
    icon: any
    description: string
}

const SERVICES = [
    {
        name: "Filtration Gateway",
        url: "http://localhost:8000/health",
        icon: Activity,
        description: "Policy enforcement, guardrails, and request routing"
    },
    {
        name: "Inference Gateway",
        url: "http://localhost:8001/v1/chat/completions",
        icon: Zap,
        description: "OpenAI-compatible API endpoint",
        method: "OPTIONS" // Just check if endpoint responds
    },
    {
        name: "Orchestration API",
        url: "http://localhost:8080/deployment/listPools/health-check",
        icon: Server,
        description: "Deployment management and compute orchestration"
    },
    {
        name: "Nosana Sidecar",
        url: "http://localhost:3000/health",
        icon: Cloud,
        description: "Nosana DePIN job management"
    }
]

async function checkHealth(url: string, method: string = "GET"): Promise<{ status: "online" | "offline"; latency: number }> {
    const start = performance.now()
    try {
        const controller = new AbortController()
        const timeoutId = setTimeout(() => controller.abort(), 5000) // 5s timeout

        const response = await fetch(url, {
            method,
            signal: controller.signal,
            mode: 'cors'
        })
        clearTimeout(timeoutId)

        const latency = Math.round(performance.now() - start)
        return {
            status: response.ok || response.status < 500 ? "online" : "offline",
            latency
        }
    } catch (error) {
        const latency = Math.round(performance.now() - start)
        return { status: "offline", latency }
    }
}

export default function Status() {
    const [lastRefresh, setLastRefresh] = useState<Date>(new Date())

    const { data: statuses, isLoading, refetch, isFetching } = useQuery({
        queryKey: ["service-health"],
        queryFn: async () => {
            const results = await Promise.all(
                SERVICES.map(async (service) => {
                    const result = await checkHealth(service.url, (service as any).method || "GET")
                    return {
                        ...service,
                        status: result.status,
                        latency: result.latency
                    }
                })
            )
            setLastRefresh(new Date())
            return results as ServiceStatus[]
        },
        refetchInterval: 30000, // Auto-refresh every 30s
        staleTime: 10000
    })

    const onlineCount = statuses?.filter(s => s.status === "online").length || 0
    const totalCount = SERVICES.length
    const allOnline = onlineCount === totalCount

    const handleRefresh = () => {
        refetch()
    }

    return (
        <div className="space-y-8 animate-in fade-in-50 duration-500">
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h2 className="text-2xl font-bold tracking-tight text-foreground">System Status</h2>
                    <p className="text-muted-foreground mt-1">
                        Monitor the health of all InferiaLLM services
                    </p>
                </div>
                <button
                    onClick={handleRefresh}
                    disabled={isFetching}
                    className={cn(
                        "flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-lg border transition-all",
                        "bg-background hover:bg-muted disabled:opacity-50"
                    )}
                >
                    <RefreshCw className={cn("w-4 h-4", isFetching && "animate-spin")} />
                    Refresh
                </button>
            </div>

            {/* Overall Status Banner */}
            <div className={cn(
                "p-6 rounded-xl border flex items-center justify-between",
                allOnline
                    ? "bg-green-500/5 border-green-500/20"
                    : "bg-amber-500/5 border-amber-500/20"
            )}>
                <div className="flex items-center gap-4">
                    <div className={cn(
                        "p-3 rounded-full",
                        allOnline ? "bg-green-500/10" : "bg-amber-500/10"
                    )}>
                        {allOnline ? (
                            <Check className="w-6 h-6 text-green-600 dark:text-green-400" />
                        ) : (
                            <AlertTriangle className="w-6 h-6 text-amber-600 dark:text-amber-400" />
                        )}
                    </div>
                    <div>
                        <h3 className={cn(
                            "font-semibold text-lg",
                            allOnline ? "text-green-700 dark:text-green-400" : "text-amber-700 dark:text-amber-400"
                        )}>
                            {allOnline ? "All Systems Operational" : "Some Services Unavailable"}
                        </h3>
                        <p className="text-sm text-muted-foreground">
                            {onlineCount} of {totalCount} services online
                        </p>
                    </div>
                </div>
                <div className="text-right text-sm text-muted-foreground">
                    <div className="flex items-center gap-1">
                        <Clock className="w-3.5 h-3.5" />
                        Last checked: {lastRefresh.toLocaleTimeString()}
                    </div>
                </div>
            </div>

            {/* Service Cards */}
            <div className="grid gap-4 md:grid-cols-2">
                {isLoading ? (
                    // Loading skeletons
                    Array.from({ length: 4 }).map((_, i) => (
                        <div key={i} className="p-6 bg-card rounded-xl border animate-pulse">
                            <div className="flex items-start justify-between">
                                <div className="flex items-center gap-3">
                                    <div className="w-10 h-10 bg-muted rounded-lg" />
                                    <div>
                                        <div className="w-32 h-5 bg-muted rounded mb-2" />
                                        <div className="w-48 h-4 bg-muted rounded" />
                                    </div>
                                </div>
                                <div className="w-20 h-6 bg-muted rounded-full" />
                            </div>
                        </div>
                    ))
                ) : (
                    statuses?.map((service) => (
                        <div
                            key={service.name}
                            className={cn(
                                "p-6 bg-card rounded-xl border transition-all hover:shadow-md",
                                service.status === "online"
                                    ? "border-l-4 border-l-green-500"
                                    : "border-l-4 border-l-red-500"
                            )}
                        >
                            <div className="flex items-start justify-between">
                                <div className="flex items-start gap-3">
                                    <div className={cn(
                                        "p-2.5 rounded-lg",
                                        service.status === "online"
                                            ? "bg-green-500/10"
                                            : "bg-red-500/10"
                                    )}>
                                        <service.icon className={cn(
                                            "w-5 h-5",
                                            service.status === "online"
                                                ? "text-green-600 dark:text-green-400"
                                                : "text-red-600 dark:text-red-400"
                                        )} />
                                    </div>
                                    <div>
                                        <h4 className="font-semibold text-foreground">{service.name}</h4>
                                        <p className="text-sm text-muted-foreground mt-0.5">
                                            {service.description}
                                        </p>
                                    </div>
                                </div>
                                <div className="flex flex-col items-end gap-1">
                                    <span className={cn(
                                        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium",
                                        service.status === "online"
                                            ? "bg-green-500/10 text-green-600 dark:text-green-400"
                                            : "bg-red-500/10 text-red-600 dark:text-red-400"
                                    )}>
                                        {service.status === "online" ? (
                                            <Check className="w-3 h-3" />
                                        ) : (
                                            <X className="w-3 h-3" />
                                        )}
                                        {service.status === "online" ? "Online" : "Offline"}
                                    </span>
                                    {service.latency !== undefined && (
                                        <span className="text-xs text-muted-foreground">
                                            {service.latency}ms
                                        </span>
                                    )}
                                </div>
                            </div>
                        </div>
                    ))
                )}
            </div>

            {/* Service Endpoints Reference */}
            <div className="bg-card rounded-xl border p-6">
                <h3 className="font-semibold mb-4 flex items-center gap-2">
                    <Database className="w-4 h-4 text-blue-500" />
                    Service Endpoints
                </h3>
                <div className="grid gap-2 text-sm font-mono">
                    {SERVICES.map(service => (
                        <div key={service.name} className="flex items-center justify-between py-2 border-b last:border-0">
                            <span className="text-muted-foreground">{service.name}</span>
                            <code className="text-xs bg-muted px-2 py-1 rounded">{service.url.replace('/health', '').replace('/v1/chat/completions', '')}</code>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    )
}
