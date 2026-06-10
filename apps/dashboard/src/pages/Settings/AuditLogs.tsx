import { useState, useEffect } from "react"
import { auditService, AUDIT_CATEGORIES, CATEGORY_COLORS, type AuditLog } from "@/services/auditService"
import { Clock, User, Shield, Search, ChevronDown, ChevronUp, AlertCircle, Tag } from "lucide-react"

export default function AuditLogs() {
    const [logs, setLogs] = useState<AuditLog[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [expandedId, setExpandedId] = useState<string | null>(null)
    const [filters, setFilters] = useState({
        action: "",
        user_id: "",
        category: "",
    })

    useEffect(() => {
        fetchLogs()
    }, [])

    const fetchLogs = async () => {
        try {
            setLoading(true)
            setError(null)
            const data = await auditService.getLogs(filters)
            setLogs(data)
        } catch (err) {
            console.error("Failed to fetch audit logs:", err)
            setLogs([])
            const detail =
                (err as any)?.response?.data?.detail ??
                (err instanceof Error ? err.message : "Unknown error")
            setError(`Failed to load audit logs: ${detail}`)
        } finally {
            setLoading(false)
        }
    }

    const handleSearch = (e: React.FormEvent) => {
        e.preventDefault()
        fetchLogs()
    }

    const formatDate = (dateStr: string) => {
        return new Date(dateStr).toLocaleString()
    }

    const toggleExpand = (id: string) => {
        setExpandedId(expandedId === id ? null : id)
    }

    const getCategoryStyle = (category: string | null) => {
        return CATEGORY_COLORS[category || ""] || "bg-secondary text-secondary-foreground"
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-2xl font-bold tracking-tight">Audit Logs</h2>
                    <p className="text-muted-foreground">
                        View and track all system activities and security events.
                    </p>
                </div>
            </div>

            {/* Filters */}
            <form onSubmit={handleSearch} className="flex gap-4 items-end bg-card p-4 rounded-lg border">
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4 flex-1">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Category</label>
                        <select
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                            value={filters.category}
                            onChange={(e) => setFilters({ ...filters, category: e.target.value })}
                        >
                            {AUDIT_CATEGORIES.map((cat) => (
                                <option key={cat.value} value={cat.value}>{cat.label}</option>
                            ))}
                        </select>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Action</label>
                        <input
                            type="text"
                            placeholder="e.g. user.login, deployment.create"
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                            value={filters.action}
                            onChange={(e) => setFilters({ ...filters, action: e.target.value })}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">User</label>
                        <input
                            type="text"
                            placeholder="Search by User ID or email"
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                            value={filters.user_id}
                            onChange={(e) => setFilters({ ...filters, user_id: e.target.value })}
                        />
                    </div>
                    <div className="flex items-end">
                        <button
                            type="submit"
                            className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 bg-primary text-primary-foreground hover:bg-primary/90 h-10 px-4 py-2 w-full md:w-auto"
                        >
                            <Search className="w-4 h-4 mr-2" />
                            Filter
                        </button>
                    </div>
                </div>
            </form>

            {/* Error banner — fetch failed or the endpoint answered with an
                unexpected shape; keep the page alive instead of crashing. */}
            {error && !loading && (
                <div className="flex items-center gap-2 p-4 mb-4 rounded-lg border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400 text-sm">
                    <AlertCircle className="w-4 h-4 shrink-0" />
                    <span>{error}</span>
                </div>
            )}

            {/* Logs List */}
            {loading ? (
                <div className="space-y-4">
                    {[...Array(5)].map((_, i) => (
                        <div key={i} className="h-16 bg-muted animate-pulse rounded-lg" />
                    ))}
                </div>
            ) : error ? null : logs.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground border rounded-lg bg-card/50">
                    <Shield className="w-12 h-12 mx-auto mb-4 opacity-50" />
                    <p>No audit logs found</p>
                    <p className="text-sm">Try adjusting your filters</p>
                </div>
            ) : (
                <div className="space-y-3">
                    {logs.map((log) => (
                        <div
                            key={log.id}
                            className="bg-card rounded-lg border shadow-sm overflow-hidden transition-colors"
                        >
                            {/* Main Row */}
                            <div
                                className="p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                                onClick={() => toggleExpand(log.id)}
                            >
                                <div className="flex items-center justify-between gap-4">
                                    <div className="flex items-center gap-4 min-w-0 flex-1">
                                        <div className="text-xs text-muted-foreground whitespace-nowrap flex items-center gap-1.5 shrink-0">
                                            <Clock className="w-3.5 h-3.5" />
                                            {formatDate(log.timestamp)}
                                        </div>

                                        {log.category && (
                                            <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold uppercase tracking-tight shrink-0 flex items-center gap-1 ${getCategoryStyle(log.category)}`}>
                                                <Tag className="w-2.5 h-2.5" />
                                                {log.category.replace("_", " ")}
                                            </span>
                                        )}

                                        <div className="flex items-center gap-3 min-w-0">
                                            <span className="font-semibold text-sm truncate">{log.action}</span>
                                            {log.resource_type && (
                                                <span className="text-[10px] px-2 py-0.5 rounded-full bg-secondary text-secondary-foreground font-medium uppercase tracking-tight shrink-0">
                                                    {log.resource_type}
                                                </span>
                                            )}
                                            {log.status === "failure" || log.status === "error" ? (
                                                <span className="text-[10px] px-2 py-0.5 rounded-full bg-destructive/10 text-destructive flex items-center gap-1 font-medium uppercase tracking-tight shrink-0">
                                                    <AlertCircle className="w-3 h-3" /> {log.status}
                                                </span>
                                            ) : (
                                                <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-500/10 text-green-600 font-medium uppercase tracking-tight shrink-0">
                                                    {log.status}
                                                </span>
                                            )}
                                        </div>
                                    </div>

                                    <div className="flex items-center gap-4 text-sm shrink-0 ml-auto">
                                        <div className="flex items-center gap-1.5 text-muted-foreground" title={log.user_id || "System"}>
                                            <User className="w-3.5 h-3.5" />
                                            <span className="text-xs max-w-[160px] truncate">
                                                {log.user_email || log.user_id || "System"}
                                            </span>
                                        </div>
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
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
                                        <div>
                                            <div className="text-xs text-muted-foreground mb-1">Actor</div>
                                            <div className="font-mono text-[11px] bg-background p-2 rounded border">
                                                {log.user_email && <div>{log.user_email}</div>}
                                                <div className="text-muted-foreground truncate" title={log.user_id || ""}>{log.user_id || "System"}</div>
                                            </div>
                                        </div>
                                        <div>
                                            <div className="text-xs text-muted-foreground mb-1">Resource ID</div>
                                            <div className="font-mono text-[10px] bg-background p-2 rounded border truncate" title={log.resource_id || ""}>
                                                {log.resource_id || "-"}
                                            </div>
                                        </div>
                                        <div>
                                            <div className="text-xs text-muted-foreground mb-1">IP Address</div>
                                            <div className="font-mono text-[10px] bg-background p-2 rounded border">
                                                {log.ip_address || "-"}
                                            </div>
                                        </div>
                                    </div>

                                    {log.details && (
                                        <div>
                                            <div className="text-xs text-muted-foreground mb-2 font-medium uppercase tracking-tight">Event Details</div>
                                            <pre className="p-3 bg-background text-cream/70 rounded-md text-[11px] overflow-x-auto font-mono whitespace-pre-wrap break-all max-h-96 overflow-y-auto">
                                                {JSON.stringify(log.details, null, 2)}
                                            </pre>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}
