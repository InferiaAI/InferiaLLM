import { useState, useEffect, useRef } from "react"
import api from "@/lib/api"
import { Monitor, RefreshCcw, ScrollText } from "lucide-react"

interface TrainingLogsProps {
    deploymentId: string
}

export default function TrainingLogs({ deploymentId }: TrainingLogsProps) {
    const [logs, setLogs] = useState<string[]>([])
    const [loading, setLoading] = useState(false)
    const [autoRefresh, setAutoRefresh] = useState(false)

    const fetchLogs = async () => {
        setLoading(true)
        try {
            // Hit the orchestration endpoint we just created
            // Using absolute URL if needed or proxy
            // Assuming api.get handles the base URL or we use full url
            const { data } = await api.get(`http://localhost:8080/deployment/logs/${deploymentId}`)
            if (data?.opStates && Array.isArray(data.opStates)) {
                // Handle Nosana nested log format
                const allLogs: string[] = []
                data.opStates.forEach((op: any) => {
                    if (op.exitCode !== undefined && op.exitCode !== 0) {
                        allLogs.push(`[SYSTEM] Operation '${op.operationId}' exited with code ${op.exitCode}`)
                    }
                    if (op.logs && Array.isArray(op.logs)) {
                        op.logs.forEach((logEntry: any) => {
                            if (typeof logEntry === 'string') allLogs.push(logEntry)
                            else if (logEntry.log) allLogs.push(logEntry.log)
                            else allLogs.push(JSON.stringify(logEntry))
                        })
                    }
                })
                setLogs(allLogs)
            } else if (data?.logs) {
                // Fallback for simple format
                if (Array.isArray(data.logs)) {
                    setLogs(data.logs.map((l: any) => typeof l === 'string' ? l : JSON.stringify(l, null, 2)))
                } else {
                    setLogs([JSON.stringify(data.logs, null, 2)])
                }
            } else {
                setLogs(["No structured logs found in response."])
            }
        } catch (error) {
            console.error("Failed to fetch training logs:", error)
            setLogs(["Failed to fetch logs."])
        } finally {
            setLoading(false)
        }
    }

    // Auto-scroll logic
    const scrollRef = useRef<HTMLDivElement>(null)

    useEffect(() => {
        if (scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight
        }
    }, [logs])

    // Process logs to handle carriage returns (\r) for progress bars
    const processLogs = (rawLogs: string[]) => {
        return rawLogs.map(line => {
            // If line contains \r, only show the part after the last \r
            const parts = line.split('\r')
            return parts[parts.length - 1]
        }).filter(line => line.trim().length > 0) // Filter empty lines if desired
    }

    const displayLogs = processLogs(logs)

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <ScrollText className="w-5 h-5 text-muted-foreground" />
                    <h3 className="font-medium">Job Output</h3>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => setAutoRefresh(!autoRefresh)}
                        className={`text-xs px-2 py-1 rounded border transition-colors ${autoRefresh ? "bg-green-500/10 text-green-600 border-green-500/30" : "bg-muted text-muted-foreground"
                            }`}
                    >
                        {autoRefresh ? "Auto-Refresh On" : "Auto-Refresh Off"}
                    </button>
                    <button
                        onClick={fetchLogs}
                        disabled={loading}
                        className="p-1.5 hover:bg-muted rounded-md transition-colors"
                        title="Refresh Logs"
                    >
                        <RefreshCcw className={`w-4 h-4 text-muted-foreground ${loading ? "animate-spin" : ""}`} />
                    </button>
                </div>
            </div>

            <div className="bg-zinc-950 rounded-lg border border-zinc-800 p-4 font-mono text-xs md:text-sm text-zinc-300 min-h-[500px] shadow-inner font-ligth">
                <div
                    ref={scrollRef}
                    className="h-[500px] w-full overflow-y-auto scrollbar-thin scrollbar-thumb-zinc-700 scrollbar-track-transparent pr-2 leading-tight"
                >
                    {displayLogs.length > 0 ? (
                        displayLogs.map((line, i) => (
                            <div key={i} className="whitespace-pre-wrap break-all py-0.5 min-h-[1.2em]">
                                <span className="text-zinc-500 mr-2 select-none">$</span>
                                {line}
                            </div>
                        ))
                    ) : (
                        <div className="text-zinc-600 italic">
                            {loading ? "Fetching logs..." : "No logs available yet..."}
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
