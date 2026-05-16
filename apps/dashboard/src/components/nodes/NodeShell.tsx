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

// Strip ANSI escape sequences. Container shells (bash/sh) emit a lot of
// cursor/colour control, none of which renders meaningfully in a plain
// <pre> — filtering keeps the output readable.
const ANSI_RE = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[=>]/g;
function stripAnsi(s: string): string {
  return s.replace(ANSI_RE, "");
}

export default function NodeShell({ nodeId, deploymentId, containerId, className }: Props) {
  const [output, setOutput] = useState<string>("");
  const [status, setStatus] = useState<"connecting" | "open" | "closed" | "error">("connecting");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [input, setInput] = useState<string>("");
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

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
    const url = `${base}/api/v1/admin/workers/${nodeId}/shell?${params.toString()}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;
    setStatus("connecting");
    setErrorMsg("");
    setOutput("");

    ws.onopen = () => setStatus("open");
    ws.onmessage = (e) => {
      try {
        const obj = JSON.parse(e.data);
        if (obj.type === "error") {
          setStatus("error");
          setErrorMsg(obj.message || "shell error");
          return;
        }
        if (obj.type === "output") {
          setOutput((prev) => {
            const next = prev + stripAnsi(obj.data ?? "");
            // Cap at ~200k chars to bound memory.
            return next.length > 200_000 ? next.slice(next.length - 180_000) : next;
          });
        }
        if (obj.type === "exit") {
          setStatus("closed");
        }
      } catch {
        setOutput((prev) => prev + e.data);
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
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [output]);

  const send = (line: string) => {
    if (status !== "open" || !wsRef.current) return;
    wsRef.current.send(JSON.stringify({ type: "stdin", data: line + "\n" }));
  };

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      send(input);
      setInput("");
    } else if (e.key === "c" && e.ctrlKey) {
      // Forward ^C as a SIGINT-equivalent input byte (the container's PTY
      // handles signal generation).
      e.preventDefault();
      if (wsRef.current && status === "open") {
        wsRef.current.send(JSON.stringify({ type: "stdin", data: "\x03" }));
      }
    }
  };

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
          <span className="text-muted-foreground">{containerId || deploymentId || "first running container"}</span>
          {errorMsg && <span className="text-red-600 dark:text-red-400">{errorMsg}</span>}
        </div>
        <span className="text-muted-foreground">Enter to send · Ctrl+C to interrupt</span>
      </div>
      <div
        ref={scrollRef}
        className="h-[440px] overflow-y-auto bg-black text-cream/90 font-mono text-xs p-4 whitespace-pre-wrap break-all"
        onClick={() => inputRef.current?.focus()}
      >
        {output ? output : <div className="text-muted-foreground">Waiting for shell…</div>}
      </div>
      <div className="border-t bg-black px-4 py-2 flex items-center gap-2">
        <span className="text-cream/60 font-mono text-sm">$</span>
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          disabled={status !== "open"}
          placeholder={status === "open" ? "Type a command…" : "Connecting…"}
          className="flex-1 bg-transparent text-cream/90 font-mono text-sm focus:outline-none disabled:opacity-50"
          autoFocus
        />
      </div>
    </div>
  );
}
