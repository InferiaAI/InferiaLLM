import React from "react"
import { useQuery } from "@tanstack/react-query"
import { Activity, Server, Database, Zap, Cloud, Check, X, RefreshCw, Clock, AlertTriangle, Shield } from "lucide-react"
import { cn } from "@/lib/utils"
import { useState } from "react"
import { MANAGEMENT_URL } from "@/lib/api"

interface ServiceHealth {
    name: string
    status: string
    latency_ms?: number
    error?: string
}

interface DependencyHealth {
    name: string
    status: string
    error?: string
}

interface SystemHealthResponse {
    status: string
    version: string
    services: ServiceHealth[]
    dependencies: DependencyHealth[]
}

const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
    "API Gateway": Activity,
    "Inference Gateway": Zap,
    "Orchestration": Server,
    "Data Service": Database,
    "Guardrail Service": Shield,
    "DePIN Sidecar": Cloud,
}

const descriptionMap: Record<string, string> = {
    "API Gateway": "Authentication, RBAC, and service routing",
    "Inference Gateway": "OpenAI-compatible LLM inference API",
    "Orchestration": "Deployment management and compute orchestration",
    "Data Service": "Document processing and vector database management",
    "Guardrail Service": "Content safety, PII detection, and policy enforcement",
    "DePIN Sidecar": "DePIN (Nosana/Akash) job management",
}

async function checkSystemHealth(): Promise<SystemHealthResponse> {
    const response = await fetch(`${MANAGEMENT_URL}/health/services`)
    if (!response.ok) {
        throw new Error("Failed to fetch system health")
    }
    return response.json()
}

export default function Status() {
    const [lastRefresh, setLastRefresh] = useState<Date>(new Date())

    const { data: healthData, isLoading, refetch, isFetching, error } = useQuery({
        queryKey: ["system-health"],
        queryFn: async () => {
            const result = await checkSystemHealth()
            setLastRefresh(new Date())
            return result
        },
        refetchInterval: 30000, // Auto-refresh every 30s
        staleTime: 10000
    })

    const statuses = healthData?.services.map(service => ({
        name: service.name,
        status: service.status as "online" | "offline" | "degraded" | "unknown",
        latency: service.latency_ms,
        icon: iconMap[service.name] || Activity,
        description: descriptionMap[service.name] || "Service",
    })) || []

    const dependencies = healthData?.dependencies.map(dep => ({
        name: dep.name,
        status: dep.status as "online" | "offline" | "unknown",
        error: dep.error,
        icon: dep.name === "PostgreSQL" ? Database : Zap,
        description: dep.name === "PostgreSQL" ? "Primary relational storage" : "Rate limiting and message broker",
    })) || []

    const onlineCount = statuses.filter(s => s.status === "online").length + dependencies.filter(d => d.status === "online").length
    const totalCount = statuses.length + dependencies.length
    const allOnline = onlineCount === totalCount && totalCount > 0

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
                    {healthData?.version && (
                        <div className="text-xs mt-1">Version: {healthData.version}</div>
                    )}
                </div>
            </div>

            {/* Error State */}
            {error && (
                <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-xl text-red-600 dark:text-red-400">
                    Failed to check system health: {error instanceof Error ? error.message : "Unknown error"}
                </div>
            )}

            {/* Service Cards */}
            <div className="grid gap-4 md:grid-cols-2">
                {isLoading ? (
                    // Loading skeletons
                    Array.from({ length: 6 }).map((_, i) => (
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
                    statuses.map((service) => (
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

            {/* Core Infrastructure */}
            <div className="space-y-4">
                <h3 className="text-lg font-semibold flex items-center gap-2">
                    <Server className="w-5 h-5 text-indigo-500" />
                    Core Infrastructure
                </h3>
                <div className="grid gap-4 md:grid-cols-2">
                    {isLoading ? (
                        Array.from({ length: 2 }).map((_, i) => (
                            <div key={i} className="p-6 bg-card rounded-xl border animate-pulse">
                                <div className="w-full h-10 bg-muted rounded" />
                            </div>
                        ))
                    ) : (
                        dependencies.map((dep) => (
                            <div
                                key={dep.name}
                                className={cn(
                                    "p-6 bg-card rounded-xl border transition-all hover:shadow-md",
                                    dep.status === "online"
                                        ? "border-l-4 border-l-indigo-500"
                                        : "border-l-4 border-l-red-500"
                                )}
                            >
                                <div className="flex items-start justify-between">
                                    <div className="flex items-start gap-3">
                                        <div className={cn(
                                            "p-2.5 rounded-lg",
                                            dep.status === "online"
                                                ? "bg-indigo-500/10"
                                                : "bg-red-500/10"
                                        )}>
                                            <dep.icon className={cn(
                                                "w-5 h-5",
                                                dep.status === "online"
                                                    ? "text-indigo-600 dark:text-indigo-400"
                                                    : "text-red-600 dark:text-red-400"
                                            )} />
                                        </div>
                                        <div>
                                            <h4 className="font-semibold text-foreground">{dep.name}</h4>
                                            <p className="text-sm text-muted-foreground mt-0.5">
                                                {dep.description}
                                            </p>
                                        </div>
                                    </div>
                                    <div className="flex flex-col items-end gap-1">
                                        <span className={cn(
                                            "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium",
                                            dep.status === "online"
                                                ? "bg-green-500/10 text-green-600 dark:text-green-400"
                                                : "bg-red-500/10 text-red-600 dark:text-red-400"
                                        )}>
                                            {dep.status === "online" ? "Healthy" : "Failed"}
                                        </span>
                                        {dep.error && (
                                            <span className="text-[10px] text-red-500 max-w-[120px] truncate" title={dep.error}>
                                                {dep.error}
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>

            {/* Service Endpoints Reference */}
            <div className="bg-card rounded-xl border p-6">
                <h3 className="font-semibold mb-4 flex items-center gap-2">
                    <Database className="w-4 h-4 text-emerald-500" />
                    Service Endpoints
                </h3>
                <div className="grid gap-2 text-sm font-mono">
                    {statuses.map(service => (
                        <div key={service.name} className="flex items-center justify-between py-2 border-b last:border-0">
                            <span className="text-muted-foreground">{service.name}</span>
                            <span className="text-xs bg-muted px-2 py-1 rounded capitalize">
                                {service.status}
                            </span>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    )
}
