import { useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/tokenStore";
import { API_GATEWAY_URL } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  nodeId: string;
  deploymentId?: string;
  containerId?: string;
  className?: string;
}

// Strips ANSI control sequences. Worker forwards container output verbatim
// (incl. color/cursor escapes); rendering them as plain text leaves visible
// ESC[ noise, so we filter the common ones before display.
const ANSI_RE = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[=>]/g;
function stripAnsi(s: string): string {
  return s.replace(ANSI_RE, "");
}

export default function NodeLogs({ nodeId, deploymentId, containerId, className }: Props) {
  const [lines, setLines] = useState<string[]>([]);
  const [status, setStatus] = useState<"connecting" | "open" | "closed" | "error">("connecting");
  const [autoScroll, setAutoScroll] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string>("");
  const wsRef = useRef<WebSocket | null>(null);
  const preRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setStatus("error");
      setErrorMsg("No auth token. Sign in again.");
      return;
    }
    const params = new URLSearchParams({ access_token: token });
    if (deploymentId) params.set("deployment", deploymentId);
    if (containerId) params.set("container", containerId);

    const base = API_GATEWAY_URL.replace(/^http/, "ws");
    const url = `${base}/api/v1/admin/workers/${nodeId}/logs?${params.toString()}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;
    setStatus("connecting");
    setErrorMsg("");

    ws.onopen = () => setStatus("open");
    ws.onmessage = (e) => {
      try {
        const obj = JSON.parse(e.data);
        if (obj.type === "error") {
          setStatus("error");
          setErrorMsg(obj.message || "stream error");
          return;
        }
        if (obj.type === "log") {
          const stream = obj.stream === "stderr" ? "ERR" : "OUT";
          const line = `[${stream}] ${stripAnsi(obj.data ?? "")}`;
          setLines((prev) => {
            // Cap at 2000 lines to avoid runaway memory in long sessions.
            const next = prev.length > 2000 ? prev.slice(prev.length - 1800) : prev;
            return [...next, line];
          });
        }
      } catch {
        setLines((prev) => [...prev, e.data]);
      }
    };
    ws.onerror = () => {
      setStatus("error");
      setErrorMsg("WebSocket connection failed.");
    };
    ws.onclose = () => setStatus((s) => (s === "error" ? s : "closed"));

    return () => {
      try { ws.close(); } catch { /* ignore */ }
    };
  }, [nodeId, deploymentId, containerId]);

  useEffect(() => {
    if (autoScroll && preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  return (
    <div className={cn("rounded-xl border bg-card text-card-foreground shadow-sm overflow-hidden", className)}>
      <div className="flex items-center justify-between px-4 py-2 border-b text-xs">
        <div className="flex items-center gap-3 font-mono">
          <span
            className={cn(
              "inline-flex items-center gap-1.5 px-2 py-0.5 rounded border",
              status === "open" && "border-emerald-500/20 text-emerald-600 dark:text-emerald-400 bg-emerald-500/10",
              status === "connecting" && "border-amber-500/20 text-amber-600 dark:text-amber-400 bg-amber-500/10",
              status === "closed" && "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
              status === "error" && "border-red-500/20 text-red-600 dark:text-red-400 bg-red-500/10",
            )}
          >
            <span
              className={cn(
                "w-1.5 h-1.5 rounded-full",
                status === "open" && "bg-emerald-500 animate-pulse",
                status === "connecting" && "bg-amber-500 animate-pulse",
                status === "closed" && "bg-muted-foreground",
                status === "error" && "bg-red-500",
              )}
            />
            {status}
          </span>
          <span className="text-muted-foreground">{lines.length} lines</span>
          {errorMsg && <span className="text-red-600 dark:text-red-400">{errorMsg}</span>}
        </div>
        <label className="inline-flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
            className="accent-ember-600"
          />
          Auto-scroll
        </label>
      </div>
      <div
        ref={preRef}
        className="h-[480px] overflow-y-auto bg-black text-cream/90 font-mono text-xs p-4 whitespace-pre-wrap break-all"
      >
        {lines.length === 0 ? (
          <div className="text-muted-foreground">Waiting for log data…</div>
        ) : (
          lines.map((l, i) => <div key={i}>{l}</div>)
        )}
      </div>
    </div>
  );
}
