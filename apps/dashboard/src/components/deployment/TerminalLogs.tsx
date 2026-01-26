import { useState, useEffect, useRef } from "react"
import { computeApi } from "@/lib/api"
import { Terminal, RefreshCcw, Wifi, WifiOff, Trash2, ChevronDown, Download, Monitor } from "lucide-react"
import { cn } from "@/lib/utils"
import { toast } from "sonner"

interface TerminalLogsProps {
    deploymentId: string
}

export default function TerminalLogs({ deploymentId }: TerminalLogsProps) {
    const [lines, setLines] = useState<any[]>([])
    // const [progressBars, setProgressBars] = useState<Record<string, any>>({}) // Removed
    const [status, setStatus] = useState<"connecting" | "connected" | "disconnected" | "error">("connecting")
    const [error, setError] = useState<string | null>(null)
    const [autoScroll, setAutoScroll] = useState(true)
    const wsRef = useRef<WebSocket | null>(null)
    const scrollRef = useRef<HTMLDivElement>(null)

    // Helper to format logs and strip ANSI
    const cleanAnsi = (str: string) => {
        if (typeof str !== 'string') return String(str);
        // Strips common ANSI escape codes
        const stripped = str.replace(/[\u001b\u009b][[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]/g, '');
        // Also handle literal \u001b if it wasn't parsed
        return stripped.replace(/\\u001b/g, '').replace(/\\u001b\[[0-9;]*m/g, '');
    }

    const formatLog = (data: any): string | string[] | null => {
        if (!data) return null;

        // Handle arrays recursively
        if (Array.isArray(data)) {
            const results = data.map(item => formatLog(item)).flat().filter(Boolean) as string[];
            return results;
        }

        // Handle Nosana IPFS result structure (opStates) if it accidentally gets through
        if (data.opStates && Array.isArray(data.opStates)) {
            return data.opStates.map((op: any) => formatLog(op.logs)).flat().filter(Boolean) as string[];
        }

        if (typeof data === 'string') {
            try {
                // If it's a string that looks like JSON, try to parse it
                if ((data.startsWith('{') || data.startsWith('['))) {
                    const parsed = JSON.parse(data);
                    // If parsing resulted in a different structure, recurse
                    if (typeof parsed !== 'string') return formatLog(parsed);
                }
            } catch (e) {
                // Not JSON or parse failed, treat as raw string
            }

            // Return cleaned string, split by newlines if necessary
            return data.split('\n').map(l => cleanAnsi(l)).filter(l => l.trim().length > 0);
        }

        // Handle objects
        if (typeof data === 'object') {
            // Check for progress updates
            // Check for progress updates - allow them to render raw
            if (data.method === 'MultiProgressBarReporter.update' || data.type === 'multi-process-bar-update') {
                // Fallthrough to standard object handling to render as text
            }

            // Extract content
            const content = data.log || data.message || data.raw;
            if (content) return formatLog(content);

            // If it's a generic object, stringify unless it has standard keys
            if (data.payload?.event) {
                const event = data.payload.event;
                return cleanAnsi(`[${event.id || 'system'}] ${event.status}${event.progress ? ': ' + event.progress : ''}`);
            }

            return cleanAnsi(JSON.stringify(data));
        }

        return cleanAnsi(String(data));
    };

    // Fetch streaming info and connect
    const connect = async () => {
        setStatus("connecting")
        setError(null)

        try {
            const { data } = await computeApi.get(`/deployment/logs/${deploymentId}/stream`)

            if (data.error) {
                throw new Error(data.error)
            }

            const { ws_url, subscription } = data

            if (!ws_url) {
                throw new Error("No WebSocket URL provided by the gateway.")
            }

            const ws = new WebSocket(ws_url)
            wsRef.current = ws

            ws.onopen = () => {
                console.log("[TerminalLogs] Connected to sidecar WS")
                setStatus("connected")
                // Subscribe to logs
                ws.send(JSON.stringify(subscription))
            }

            ws.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data)
                    if (message.type === 'log') {
                        const rawData = message.data;


                        const formatted = formatLog(rawData);
                        if (formatted) {
                            const newLines = Array.isArray(formatted) ? formatted : [formatted];
                            setLines(prev => [...prev.slice(-1999 + newLines.length), ...newLines]);
                        }
                    } else if (message.type === 'error') {
                        setError(message.message)
                    }
                } catch (e) {
                    setLines(prev => [...prev.slice(-1999), cleanAnsi(event.data)])
                }
            }

            ws.onerror = (e) => {
                console.error("[TerminalLogs] WS Error:", e)
                setStatus("error")
                setError("WebSocket connection failed.")
            }

            ws.onclose = () => {
                console.log("[TerminalLogs] WS Closed")
                if (status !== "error") {
                    setStatus("disconnected")
                }
            }

        } catch (err: any) {
            console.error("Failed to setup log stream:", err)
            setStatus("error")
            setError(err.message || "Failed to initialize log stream.")
            toast.error("Log stream initialization failed")
        }
    }

    useEffect(() => {
        connect()
        return () => {
            if (wsRef.current) {
                wsRef.current.close()
            }
        }
    }, [deploymentId])

    useEffect(() => {
        if (autoScroll && scrollRef.current) {
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight
        }
    }, [lines, autoScroll])

    const clearLogs = () => {
        setLines([])
    }

    const downloadLogs = () => {
        const text = lines.join('\n')
        const blob = new Blob([text], { type: 'text/plain' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `logs-${deploymentId}.txt`
        a.click()
        URL.revokeObjectURL(url)
    }

    return (
        <div className="flex flex-col h-[600px] bg-zinc-950 rounded-xl border border-zinc-800 overflow-hidden shadow-2xl">
            {/* Terminal Header */}
            <div className="flex items-center justify-between px-4 py-2 border-b border-zinc-800 bg-zinc-900/50">
                <div className="flex items-center gap-3">
                    <div className="flex items-center gap-2">
                        <Terminal className="w-4 h-4 text-zinc-400" />
                        <span className="text-xs font-mono font-medium text-zinc-400">node@nosana-runtime:~</span>
                    </div>
                    {status === "connected" ? (
                        <div className="flex items-center gap-1.5 ml-2 transition-all duration-500">
                            <span className="relative flex h-2 w-2">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                                <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
                            </span>
                            <span className="text-[10px] font-mono text-green-500/80 uppercase tracking-widest font-bold">Live Stream</span>
                        </div>
                    ) : status === "connecting" ? (
                        <div className="flex items-center gap-1.5 ml-2">
                            <RefreshCcw className="w-3.5 h-3.5 text-yellow-500 animate-spin" />
                            <span className="text-[10px] font-mono text-yellow-500 uppercase tracking-widest font-bold">Connecting</span>
                        </div>
                    ) : (
                        <div className="flex items-center gap-1.5 ml-2">
                            <WifiOff className="w-3.5 h-3.5 text-red-500" />
                            <span className="text-[10px] font-mono text-red-500 uppercase tracking-widest font-bold">Stopped</span>
                        </div>
                    )}
                </div>

                <div className="flex items-center gap-3">
                    <button
                        onClick={clearLogs}
                        className="p-1.5 hover:bg-white/5 rounded transition-all group"
                        title="Clear Buffer"
                    >
                        <Trash2 className="w-3.5 h-3.5 text-zinc-500 group-hover:text-red-400" />
                    </button>
                    <button
                        onClick={downloadLogs}
                        className="p-1.5 hover:bg-white/5 rounded transition-all group"
                        title="Download Logs"
                    >
                        <Download className="w-3.5 h-3.5 text-zinc-500 group-hover:text-primary" />
                    </button>
                    <button
                        onClick={() => setAutoScroll(!autoScroll)}
                        className={cn(
                            "p-1.5 rounded transition-all flex items-center gap-1.5 px-2",
                            autoScroll ? "bg-primary/10 text-primary border border-primary/20" : "hover:bg-white/5 text-zinc-500"
                        )}
                        title="Toggle Auto-scroll"
                    >
                        <ChevronDown className={cn("w-3.5 h-3.5", autoScroll && "animate-bounce")} />
                        <span className="text-[10px] font-bold uppercase tracking-tight">AutoScroll</span>
                    </button>
                    <button
                        onClick={connect}
                        className="p-1.5 hover:bg-white/5 rounded transition-all"
                    >
                        <RefreshCcw className="w-3.5 h-3.5 text-zinc-500" />
                    </button>
                </div>
            </div>

            {/* Terminal Content */}
            <div
                ref={scrollRef}
                className="flex-1 p-4 font-mono text-xs md:text-[13px] overflow-y-auto scrollbar-thin scrollbar-thumb-zinc-800 scrollbar-track-transparent selection:bg-primary/30"
            >
                <div className="space-y-0.5">
                    {lines.map((content, i) => (
                        <div key={i} className="flex gap-3 text-zinc-300 leading-relaxed hover:bg-white/5 px-2 -mx-2 rounded transition-colors group">
                            <span className="text-zinc-600 select-none shrink-0 w-8 group-hover:text-zinc-500 transition-colors uppercase text-[10px] flex items-center">{i + 1}</span>
                            <span className="whitespace-pre-wrap break-all">{content}</span>
                        </div>
                    ))}

                    {status === "connected" && (
                        <div className="flex gap-3 text-zinc-300 leading-relaxed h-6 items-center">
                            <span className="text-zinc-600 select-none shrink-0 w-8">{lines.length + 1}</span>
                            <span className="w-1.5 h-4 bg-primary/80 animate-pulse ml-0.5" />
                        </div>
                    )}

                    {lines.length === 0 && (
                        <div className="h-full flex flex-col items-center justify-center text-zinc-600 space-y-4 py-20 uppercase tracking-widest opacity-50">
                            {status === "connecting" ? (
                                <>
                                    <RefreshCcw className="w-8 h-8 animate-spin opacity-20" />
                                    <p className="text-xs font-bold animate-pulse">Initializing Stream...</p>
                                </>
                            ) : error ? (
                                <>
                                    <div className="p-3 rounded-full bg-red-500/10 border border-red-500/20">
                                        <WifiOff className="w-8 h-8 text-red-500/50" />
                                    </div>
                                    <div className="text-center space-y-2">
                                        <p className="text-xs font-bold text-red-500">{error}</p>
                                        <button
                                            onClick={connect}
                                            className="text-[10px] px-4 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-md transition-all border border-zinc-700 font-bold"
                                        >
                                            RECONNECT
                                        </button>
                                    </div>
                                </>
                            ) : (
                                <>
                                    <Monitor className="w-8 h-8 opacity-10" />
                                    <p className="text-xs font-bold">Waiting for deployment logs...</p>
                                </>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* Terminal Footer Info */}
            <div className="px-4 py-1.5 bg-zinc-900/80 border-t border-zinc-800 flex justify-between items-center">
                <div className="flex gap-4">
                    <span className="text-[10px] font-mono text-zinc-500 uppercase tracking-tighter">Encoding: UTF-8</span>
                    <span className="text-[10px] font-mono text-zinc-500 uppercase tracking-tighter">Rows: {lines.length}</span>
                </div>
                <div className="text-[10px] font-mono text-zinc-500 uppercase tracking-tighter flex items-center gap-1">
                    <div className="w-1.5 h-1.5 rounded-full bg-primary/40" />
                    Inferia Runtime v1.0.0
                </div>
            </div>
        </div>
    )
}
