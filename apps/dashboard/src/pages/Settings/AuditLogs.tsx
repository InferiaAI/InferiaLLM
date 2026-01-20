import { useState, useEffect } from "react"
import { auditService, type AuditLog } from "@/services/auditService"
import { Clock, User, Shield, Search, ChevronDown, ChevronUp, AlertCircle } from "lucide-react"

export default function AuditLogs() {
    const [logs, setLogs] = useState<AuditLog[]>([])
    const [loading, setLoading] = useState(true)
    const [expandedId, setExpandedId] = useState<string | null>(null)
    const [filters, setFilters] = useState({
        action: "",
        user_id: "",
    })

    useEffect(() => {
        fetchLogs()
    }, [])

    const fetchLogs = async () => {
        try {
            setLoading(true)
            const data = await auditService.getLogs(filters)
            setLogs(data)
        } catch (error) {
            console.error("Failed to fetch audit logs:", error)
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
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 flex-1">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Action</label>
                        <input
                            type="text"
                            placeholder="e.g. login, model_inference"
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                            value={filters.action}
                            onChange={(e) => setFilters({ ...filters, action: e.target.value })}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">User ID</label>
                        <input
                            type="text"
                            placeholder="Search by User ID"
                            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                            value={filters.user_id}
                            onChange={(e) => setFilters({ ...filters, user_id: e.target.value })}
                        />
                    </div>
                    <div className="flex items-end">
                        <button
                            type="submit"
                            className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground hover:bg-primary/90 h-10 px-4 py-2 w-full md:w-auto"
                        >
                            <Search className="w-4 h-4 mr-2" />
                            Filter Logs
                        </button>
                    </div>
                </div>
            </form>

            {/* Logs List */}
            {loading ? (
                <div className="space-y-4">
                    {[...Array(5)].map((_, i) => (
                        <div key={i} className="h-16 bg-muted animate-pulse rounded-lg" />
                    ))}
                </div>
            ) : logs.length === 0 ? (
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
                            className="bg-card rounded-lg border shadow-sm overflow-hidden transition-all"
                        >
                            {/* Main Row */}
                            <div
                                className="p-4 cursor-pointer hover:bg-muted/30 transition-colors"
                                onClick={() => toggleExpand(log.id)}
                            >
                                <div className="flex items-center justify-between gap-4">
                                    <div className="flex items-center gap-6 min-w-0">
                                        <div className="text-xs text-muted-foreground whitespace-nowrap flex items-center gap-1.5">
                                            <Clock className="w-3.5 h-3.5" />
                                            {formatDate(log.timestamp)}
                                        </div>

                                        <div className="flex items-center gap-3">
                                            <span className="font-medium text-sm">{log.action}</span>
                                            <span className="text-xs px-2 py-0.5 rounded-full bg-secondary text-secondary-foreground">
                                                {log.resource_type}
                                            </span>
                                            {log.status === "failure" || log.status === "error" ? (
                                                <span className="text-xs px-2 py-0.5 rounded-full bg-destructive/10 text-destructive flex items-center gap-1">
                                                    <AlertCircle className="w-3 h-3" /> {log.status}
                                                </span>
                                            ) : (
                                                <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/10 text-green-600">
                                                    {log.status}
                                                </span>
                                            )}
                                        </div>
                                    </div>

                                    <div className="flex items-center gap-4 text-sm">
                                        <div className="flex items-center gap-1.5 text-muted-foreground" title="User">
                                            <User className="w-3.5 h-3.5" />
                                            <span className="font-mono text-xs">{log.user_id || "System"}</span>
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
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                                        <div>
                                            <div className="text-xs text-muted-foreground mb-1">Resource ID</div>
                                            <div className="font-mono text-xs bg-background p-2 rounded border">
                                                {log.resource_id || "-"}
                                            </div>
                                        </div>
                                        <div>
                                            <div className="text-xs text-muted-foreground mb-1">IP Address</div>
                                            <div className="font-mono text-xs bg-background p-2 rounded border">
                                                {log.ip_address || "-"}
                                            </div>
                                        </div>
                                    </div>

                                    {log.details && (
                                        <div>
                                            <div className="text-xs text-muted-foreground mb-2">Event Details</div>
                                            <pre className="p-3 bg-muted rounded-md text-xs overflow-x-auto font-mono">
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
