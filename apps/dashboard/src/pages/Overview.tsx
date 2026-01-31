import { useQuery } from "@tanstack/react-query"
import api, { computeApi } from "@/lib/api"
import { useAuth } from "@/context/AuthContext"
import { Users, Server, Rocket, CheckCircle2, Activity, Clock, AlertCircle, Circle, Zap, ExternalLink, Shield } from "lucide-react"
import { cn } from "@/lib/utils"
import { OverviewSkeleton } from "@/components/skeletons/OverviewSkeleton"
import { Link } from "react-router-dom"

interface Organization {
    id: string
    name: string
    api_key: string | null
    created_at: string
}

export default function Overview() {
    const { user } = useAuth()

    const { data: org, isLoading: orgLoading, error: orgError } = useQuery({
        queryKey: ["organization"],
        queryFn: async () => {
            const { data } = await api.get<Organization>("/management/organizations/me")
            return data
        }
    })

    const { data: poolsData, isLoading: poolsLoading, error: poolsError } = useQuery({
        queryKey: ["pools"],
        queryFn: async () => {
            const targetOrgId = user?.org_id || org?.id;
            if (!targetOrgId) return { pools: [] }
            const { data } = await computeApi.get(`/deployment/listPools/${targetOrgId}`)
            return data
        },
        enabled: !!(user?.org_id || org?.id)
    })

    const { data: deployments, isLoading: deploymentsLoading, error: deploymentsError } = useQuery({
        queryKey: ["deployments"],
        queryFn: async () => {
            const targetOrgId = user?.org_id || org?.id;
            if (!targetOrgId) return []
            const { data } = await computeApi.get(`/deployment/deployments?org_id=${targetOrgId}`)
            return data?.deployments || data || []
        },
        enabled: !!(user?.org_id || org?.id)
    })

    const { data: recentLogs, isLoading: logsLoading } = useQuery({
        queryKey: ["recent-logs"],
        queryFn: async () => {
            const { data } = await api.get("/management/deployments/recent-logs?limit=5")
            return data
        },
        enabled: !!(user?.org_id || org?.id)
    })

    const { data: auditLogs } = useQuery({
        queryKey: ["audit-logs"],
        queryFn: async () => {
            try {
                const { data } = await api.get("/management/audit/logs?limit=5")
                return data
            } catch (e) {
                return [] // Audit logs may require admin role
            }
        },
        enabled: !!(user?.org_id || org?.id)
    })

    const isLoading = orgLoading || poolsLoading || deploymentsLoading || logsLoading
    const error = orgError || poolsError || deploymentsError

    // Derive running jobs from deployments
    const runningJobs = Array.isArray(deployments)
        ? deployments.filter((d: any) => ['RUNNING', 'PENDING', 'DEPLOYING'].includes(d.state?.toUpperCase()))
        : []

    if (isLoading) {
        return <OverviewSkeleton />
    }

    if (error) {
        return (
            <div className="p-6 text-destructive bg-destructive/5 rounded-xl border border-destructive/20 flex items-center gap-3">
                <AlertCircle className="w-5 h-5" />
                <span>Failed to load dashboard data. Please check your connection or try again.</span>
            </div>
        )
    }

    return (
        <div className="space-y-8 animate-in fade-in-50 duration-500">
            <div className="flex flex-col gap-1">
                <h2 className="text-2xl font-bold tracking-tight text-foreground">Overview</h2>
                <p className="text-muted-foreground">Welcome back, {user?.email}</p>
            </div>

            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
                <StatsCard
                    label="Compute Pools"
                    value={poolsData?.pools?.length || 0}
                    status={poolsData?.pools?.length > 0 ? "Active" : "No pools"}
                    icon={Server}
                    className="border-l-4 border-l-blue-500"
                />
                <StatsCard
                    label="Deployments"
                    value={Array.isArray(deployments) ? deployments.length : 0}
                    status={runningJobs.length > 0 ? `${runningJobs.length} running` : "No active"}
                    icon={Rocket}
                    className="border-l-4 border-l-purple-500"
                />
                <StatsCard
                    label="Team Size"
                    value={1}
                    status="Active"
                    icon={Users}
                    className="border-l-4 border-l-emerald-500"
                />
                <StatsCard
                    label="Total Requests"
                    value={recentLogs?.length || 0}
                    status="Last 24h"
                    icon={Activity}
                    className="border-l-4 border-l-orange-500"
                />
            </div>

            {/* Running Jobs & Pool Status Row */}
            <div className="grid gap-6 lg:grid-cols-2">
                {/* Running Jobs */}
                <div className="bg-card rounded-xl border shadow-sm">
                    <div className="p-4 border-b flex items-center justify-between">
                        <h3 className="font-semibold flex items-center gap-2">
                            <Zap className="w-4 h-4 text-amber-500" />
                            Running Jobs
                            {runningJobs.length > 0 && (
                                <span className="text-xs bg-green-500/10 text-green-600 dark:text-green-400 px-2 py-0.5 rounded-full">
                                    {runningJobs.length} active
                                </span>
                            )}
                        </h3>
                        <Link to="/dashboard/deployments" className="text-xs text-blue-600 dark:text-blue-400 hover:underline">
                            View all →
                        </Link>
                    </div>
                    <div className="divide-y">
                        {runningJobs.length === 0 ? (
                            <div className="p-8 text-center text-muted-foreground">
                                <Circle className="w-8 h-8 mx-auto mb-2 opacity-20" />
                                <p className="text-sm">No active deployments</p>
                            </div>
                        ) : (
                            runningJobs.slice(0, 5).map((job: any) => (
                                <div key={job.deployment_id} className="p-4 flex items-center justify-between hover:bg-muted/30 transition-colors">
                                    <div className="flex items-center gap-3">
                                        <div className={cn(
                                            "w-2 h-2 rounded-full",
                                            job.state === "RUNNING" ? "bg-green-500 animate-pulse" : "bg-amber-500"
                                        )} />
                                        <div>
                                            <div className="font-medium text-sm">{job.model_name}</div>
                                            <div className="text-xs text-muted-foreground">{job.state}</div>
                                        </div>
                                    </div>
                                    <Link
                                        to={`/dashboard/deployments/${job.deployment_id}`}
                                        className="text-xs text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1"
                                    >
                                        Details <ExternalLink className="w-3 h-3" />
                                    </Link>
                                </div>
                            ))
                        )}
                    </div>
                </div>

                {/* Pool Status */}
                <div className="bg-card rounded-xl border shadow-sm">
                    <div className="p-4 border-b flex items-center justify-between">
                        <h3 className="font-semibold flex items-center gap-2">
                            <Server className="w-4 h-4 text-blue-500" />
                            Pool Status
                        </h3>
                        <Link to="/dashboard/compute/pools" className="text-xs text-blue-600 dark:text-blue-400 hover:underline">
                            Manage →
                        </Link>
                    </div>
                    <div className="divide-y">
                        {(!poolsData?.pools || poolsData.pools.length === 0) ? (
                            <div className="p-8 text-center text-muted-foreground">
                                <Server className="w-8 h-8 mx-auto mb-2 opacity-20" />
                                <p className="text-sm">No compute pools configured</p>
                                <Link to="/dashboard/compute/pools/new" className="text-xs text-blue-600 dark:text-blue-400 hover:underline mt-2 inline-block">
                                    Create one →
                                </Link>
                            </div>
                        ) : (
                            poolsData.pools.slice(0, 5).map((pool: any) => (
                                <div key={pool.pool_id} className="p-4 flex items-center justify-between hover:bg-muted/30 transition-colors">
                                    <div className="flex items-center gap-3">
                                        <div className={cn(
                                            "p-2 rounded-lg",
                                            pool.is_active ? "bg-green-500/10" : "bg-zinc-500/10"
                                        )}>
                                            <Server className={cn(
                                                "w-4 h-4",
                                                pool.is_active ? "text-green-600 dark:text-green-400" : "text-zinc-500"
                                            )} />
                                        </div>
                                        <div>
                                            <div className="font-medium text-sm">{pool.pool_name}</div>
                                            <div className="text-xs text-muted-foreground capitalize">{pool.provider}</div>
                                        </div>
                                    </div>
                                    <span className={cn(
                                        "text-xs px-2 py-1 rounded-full font-medium",
                                        pool.is_active
                                            ? "bg-green-500/10 text-green-600 dark:text-green-400"
                                            : "bg-zinc-500/10 text-zinc-500"
                                    )}>
                                        {pool.is_active ? "Active" : "Inactive"}
                                    </span>
                                </div>
                            ))
                        )}
                    </div>
                </div>
            </div>

            {/* Audit Logs */}
            {auditLogs && auditLogs.length > 0 && (
                <div className="space-y-4">
                    <h3 className="font-semibold text-lg flex items-center gap-2">
                        <Shield className="w-4 h-4 text-purple-500" />
                        Recent Activity (Audit)
                    </h3>
                    <div className="border rounded-xl overflow-hidden bg-card shadow-sm">
                        <div className="divide-y">
                            {auditLogs.map((log: any) => (
                                <div key={log.id} className="px-6 py-3 flex items-center justify-between hover:bg-muted/30 transition-colors">
                                    <div className="flex items-center gap-3">
                                        <div className="w-2 h-2 rounded-full bg-purple-500" />
                                        <span className="font-mono text-xs bg-muted px-2 py-0.5 rounded">{log.action}</span>
                                        <span className="text-sm text-muted-foreground">{log.resource_type}</span>
                                    </div>
                                    <span className="text-xs text-muted-foreground">
                                        {new Date(log.timestamp).toLocaleString()}
                                    </span>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}

            {/* Recent Inferences */}
            <div className="space-y-4">
                <div className="flex items-center justify-between">
                    <h3 className="font-semibold text-lg flex items-center gap-2">
                        Recent Inferences
                    </h3>
                </div>

                <div className="border rounded-xl overflow-hidden bg-card shadow-sm">
                    <table className="w-full text-sm text-left">
                        <thead className="bg-muted/40 border-b text-muted-foreground font-medium">
                            <tr>
                                <th className="px-6 py-4 font-medium">Timestamp</th>
                                <th className="px-6 py-4 font-medium">Deployment</th>
                                <th className="px-6 py-4 font-medium">Tokens (In/Out)</th>
                                <th className="px-6 py-4 font-medium">Latency</th>
                                <th className="px-6 py-4 font-medium">Status</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y">
                            {recentLogs?.map((log: any) => (
                                <tr key={log.id} className="hover:bg-muted/30 transition-colors group">
                                    <td className="px-6 py-4 text-muted-foreground font-mono text-xs">
                                        <div className="flex items-center gap-2">
                                            <Clock className="w-3.5 h-3.5 opacity-70" />
                                            {new Date(log.created_at).toLocaleString()}
                                        </div>
                                    </td>
                                    <td className="px-6 py-4">
                                        <div className="flex items-center gap-2">
                                            <div className="w-2 h-2 rounded-full bg-purple-500" />
                                            <span className="font-medium text-foreground">
                                                {log.deployment_id.split('-')[0]}...
                                            </span>
                                        </div>
                                    </td>
                                    <td className="px-6 py-4">
                                        <div className="flex items-center gap-2 font-mono text-xs">
                                            <span className="text-muted-foreground">In:</span>
                                            <span className="text-foreground font-medium">{log.prompt_tokens}</span>
                                            <span className="text-muted-foreground mx-1">|</span>
                                            <span className="text-muted-foreground">Out:</span>
                                            <span className="text-foreground font-medium">{log.completion_tokens}</span>
                                        </div>
                                    </td>
                                    <td className="px-6 py-4 text-muted-foreground font-mono text-xs">
                                        {log.latency_ms ? `${log.latency_ms}ms` : "-"}
                                    </td>
                                    <td className="px-6 py-4">
                                        <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-green-500/10 text-green-600 dark:text-green-400 border border-green-500/20">
                                            Success
                                        </span>
                                    </td>
                                </tr>
                            ))}
                            {(!recentLogs || recentLogs.length === 0) && (
                                <tr>
                                    <td colSpan={5} className="px-6 py-12 text-center text-muted-foreground">
                                        <div className="flex flex-col items-center gap-2">
                                            <Activity className="w-8 h-8 opacity-20" />
                                            <p>No recent activity found.</p>
                                        </div>
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    )
}

function StatsCard({ label, value, status, icon: Icon, className }: any) {
    return (
        <div className={cn("p-6 bg-card rounded-xl border shadow-sm hover:shadow-md transition-all hover:-translate-y-0.5", className)}>
            <div className="flex items-start justify-between mb-4">
                <div className="p-2 rounded-lg bg-background border shadow-sm">
                    <Icon className="w-5 h-5 text-muted-foreground" />
                </div>
                <div className="text-right">
                    <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">{label}</div>
                    <div className="text-2xl font-bold text-foreground">{value}</div>
                </div>
            </div>

            <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />
                <span>{status}</span>
            </div>
        </div>
    )
}