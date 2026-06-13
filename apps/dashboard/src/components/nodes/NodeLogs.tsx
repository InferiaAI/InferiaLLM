import { useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/tokenStore";
import { toWsUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  getProvisioningLogs,
  getEC2Console,
  type ProvisioningEvent,
} from "@/services/provisioningService";

interface Props {
  nodeId: string;
  deploymentId?: string;
  containerId?: string;
  className?: string;
  nodeProvider?: string;
  nodeState?: string;
}

// Strips ANSI control sequences. Worker forwards container output verbatim
// (incl. color/cursor escapes); rendering them as plain text leaves visible
// ESC[ noise, so we filter the common ones before display.
const ANSI_RE = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[=>]/g;
function stripAnsi(s: string): string {
  return s.replace(ANSI_RE, "");
}

export default function NodeLogs(props: Props) {
  const isAwsProvisioning =
    props.nodeProvider === "aws" &&
    (props.nodeState === "provisioning" || props.nodeState === "terminated");

  if (isAwsProvisioning) {
    return <AwsProvisioningLogs nodeId={props.nodeId} className={props.className} />;
  }

  return (
    <NodeLogsWS
      nodeId={props.nodeId}
      deploymentId={props.deploymentId}
      containerId={props.containerId}
      className={props.className}
    />
  );
}

// ─── WebSocket-based log viewer (original implementation) ────────────────────

function NodeLogsWS({
  nodeId,
  deploymentId,
  containerId,
  className,
}: {
  nodeId: string;
  deploymentId?: string;
  containerId?: string;
  className?: string;
}) {
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

    const url = toWsUrl(`/v1/admin/workers/${nodeId}/logs?${params.toString()}`);

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

// ─── AWS provisioning log viewer (polls REST; no WebSocket) ──────────────────

function AwsProvisioningLogs({ nodeId, className }: { nodeId: string; className?: string }) {
  const [events, setEvents] = useState<ProvisioningEvent[]>([]);
  const [consoleLogs, setConsoleLogs] = useState<string[] | null>(null);
  const [consoleLoading, setConsoleLoading] = useState(false);
  const afterRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await getProvisioningLogs(nodeId, afterRef.current);
        if (cancelled) return;
        if (r.events.length) {
          setEvents(prev => [...prev, ...r.events]);
          if (r.next_after != null) {
            afterRef.current = r.next_after;
          }
        }
      } catch { /* swallow */ }
    };
    void tick();
    const h = window.setInterval(() => void tick(), 2000);
    return () => { cancelled = true; window.clearInterval(h); };
  }, [nodeId]);

  const fetchConsole = async () => {
    setConsoleLoading(true);
    try {
      const c = await getEC2Console(nodeId);
      setConsoleLogs(c.logs);
    } finally { setConsoleLoading(false); }
  };

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex justify-end">
        <button
          onClick={() => void fetchConsole()}
          disabled={consoleLoading}
          className="h-8 px-3 border rounded-md text-xs hover:bg-muted/50"
        >
          {consoleLoading ? "Fetching…" : "Fetch EC2 console"}
        </button>
      </div>
      {consoleLogs && (
        <details open className="rounded-md border bg-card p-3">
          <summary className="text-xs font-semibold cursor-pointer">
            EC2 console output ({consoleLogs.length} lines)
          </summary>
          <pre className="mt-2 text-[11px] font-mono whitespace-pre-wrap break-all max-h-72 overflow-auto">
            {consoleLogs.join("\n")}
          </pre>
        </details>
      )}
      <div className="rounded-md border bg-card font-mono text-[11px] p-3 max-h-96 overflow-auto">
        {events.length === 0 ? (
          <div className="text-muted-foreground">Waiting for events…</div>
        ) : (
          events.map(e => (
            <div key={e.id} className={cn(
              "py-0.5",
              e.status === "failed" && "text-red-500",
              e.phase === "cloud_init" && "text-muted-foreground",
            )}>
              <span className="opacity-60">[{e.phase}/{e.status}]</span>{" "}
              {e.message ?? ""}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
