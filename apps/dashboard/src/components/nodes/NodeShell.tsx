import { useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/tokenStore";
import { API_GATEWAY_URL } from "@/lib/api";
import { cn } from "@/lib/utils";

interface NodeShellProps {
  nodeId: string;
  deploymentId?: string;
  containerId?: string;
  className?: string;
  nodeState?: string;
  currentPhase?: string | null;
}

// Strip ANSI escape sequences. Container shells (bash/sh) emit a lot of
// cursor/colour control, none of which renders meaningfully in a plain
// <pre> — filtering keeps the output readable.
const ANSI_RE = /\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[=>]/g;
function stripAnsi(s: string): string {
  return s.replace(ANSI_RE, "");
}

const SHELL_OPTIONS: { label: string; value: string }[] = [
  { label: "/bin/bash", value: "/bin/bash" },
  { label: "/bin/sh", value: "/bin/sh" },
  { label: "/bin/ash", value: "/bin/ash" },
  { label: "/bin/zsh", value: "/bin/zsh" },
  { label: "/usr/bin/python3", value: "/usr/bin/python3" },
  { label: "Custom…", value: "__custom__" },
];

const USER_OPTIONS: { label: string; value: string }[] = [
  { label: "Container default", value: "" },
  { label: "root (uid 0)", value: "root" },
  { label: "nobody", value: "nobody" },
  { label: "1000:1000", value: "1000:1000" },
  { label: "Custom…", value: "__custom__" },
];

// Public wrapper — conditionally renders a placeholder or the live WS shell.
// Keeping the early-return in this thin wrapper (which has NO hooks) avoids
// the React "Rendered more hooks than during the previous render" crash that
// occurs when a component transitions between two code-paths that call
// different numbers of hooks.
export default function NodeShell({ nodeId, deploymentId, containerId, className, nodeState, currentPhase }: NodeShellProps) {
  if (nodeState && nodeState !== "ready") {
    return (
      <div className="rounded-md border bg-card p-6 text-sm text-muted-foreground">
        Shell available once the worker registers. Currently {currentPhase ?? "pending"}…
      </div>
    );
  }
  return <NodeShellWS nodeId={nodeId} deploymentId={deploymentId} containerId={containerId} className={className} />;
}

// All hooks and WS plumbing live here. This component is only ever mounted
// when nodeState is "ready" (or absent), so hook count is always stable.
function NodeShellWS({ nodeId, deploymentId, containerId, className }: {
  nodeId: string;
  deploymentId?: string;
  containerId?: string;
  className?: string;
}) {
  const [output, setOutput] = useState<string>("");
  const [status, setStatus] = useState<"idle" | "connecting" | "open" | "closed" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState<string>("");
  const [input, setInput] = useState<string>("");

  // Pre-connect knobs.
  const [shellSelect, setShellSelect] = useState<string>("/bin/bash");
  const [shellCustom, setShellCustom] = useState<string>("");
  const [userSelect, setUserSelect] = useState<string>("");
  const [userCustom, setUserCustom] = useState<string>("");
  // generation counter — incrementing it triggers a (re)connect with the
  // current selections.
  const [connectGen, setConnectGen] = useState<number>(0);

  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Resolve the values the user actually picked.
  const effectiveShell = shellSelect === "__custom__" ? shellCustom.trim() : shellSelect;
  const effectiveUser = userSelect === "__custom__" ? userCustom.trim() : userSelect;

  useEffect(() => {
    if (connectGen === 0) return; // wait for Connect click
    const token = getToken();
    if (!token) {
      setStatus("error");
      setErrorMsg("No auth token. Sign in again.");
      return;
    }
    if (!effectiveShell) {
      setStatus("error");
      setErrorMsg("Pick a shell binary first.");
      return;
    }

    const params = new URLSearchParams({ access_token: token });
    if (deploymentId) params.set("deployment", deploymentId);
    if (containerId) params.set("container", containerId);
    params.set("shell", effectiveShell);
    if (effectiveUser) params.set("user", effectiveUser);

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
  // We intentionally key the connect on `connectGen`, not on the form
  // fields — re-rendering on each keystroke must not drop the live
  // shell. Operators bump connectGen via the Connect button.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectGen]);

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
      e.preventDefault();
      if (wsRef.current && status === "open") {
        wsRef.current.send(JSON.stringify({ type: "stdin", data: "\x03" }));
      }
    }
  };

  const handleConnect = () => {
    if (wsRef.current) {
      try { wsRef.current.close(); } catch { /* ignore */ }
    }
    setConnectGen((g) => g + 1);
  };

  const handleDisconnect = () => {
    if (wsRef.current) {
      try { wsRef.current.close(); } catch { /* ignore */ }
    }
    setStatus("closed");
  };

  const connected = status === "open";

  return (
    <div className={cn("rounded-xl border bg-card text-card-foreground shadow-sm overflow-hidden", className)}>
      {/* Pre-connect / always-visible config bar. */}
      <div className="px-4 py-3 border-b bg-muted/30 flex flex-wrap items-end gap-3 text-xs">
        <div className="flex flex-col gap-1 min-w-[160px]">
          <label className="text-muted-foreground uppercase tracking-wider text-[10px]">Entrypoint</label>
          <select
            value={shellSelect}
            onChange={(e) => setShellSelect(e.target.value)}
            disabled={connected}
            className="px-2 py-1.5 rounded-md border bg-background text-foreground font-mono disabled:opacity-50"
          >
            {SHELL_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          {shellSelect === "__custom__" && (
            <input
              type="text"
              value={shellCustom}
              onChange={(e) => setShellCustom(e.target.value)}
              disabled={connected}
              placeholder="/usr/local/bin/myshell"
              className="px-2 py-1.5 rounded-md border bg-background text-foreground font-mono disabled:opacity-50"
            />
          )}
        </div>

        <div className="flex flex-col gap-1 min-w-[160px]">
          <label className="text-muted-foreground uppercase tracking-wider text-[10px]">User</label>
          <select
            value={userSelect}
            onChange={(e) => setUserSelect(e.target.value)}
            disabled={connected}
            className="px-2 py-1.5 rounded-md border bg-background text-foreground font-mono disabled:opacity-50"
          >
            {USER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          {userSelect === "__custom__" && (
            <input
              type="text"
              value={userCustom}
              onChange={(e) => setUserCustom(e.target.value)}
              disabled={connected}
              placeholder="appuser or 1001:1001"
              className="px-2 py-1.5 rounded-md border bg-background text-foreground font-mono disabled:opacity-50"
            />
          )}
        </div>

        <div className="flex items-center gap-2 ml-auto">
          {!connected ? (
            <button
              onClick={handleConnect}
              disabled={!effectiveShell || status === "connecting"}
              className="px-3 py-1.5 rounded-md border bg-ember-600 text-white hover:bg-ember-700 disabled:opacity-50 disabled:cursor-not-allowed font-medium"
            >
              {status === "connecting" ? "Connecting…" : connectGen === 0 ? "Connect" : "Reconnect"}
            </button>
          ) : (
            <button
              onClick={handleDisconnect}
              className="px-3 py-1.5 rounded-md border bg-background hover:bg-muted/50 font-medium"
            >
              Disconnect
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between px-4 py-2 border-b text-xs">
        <div className="flex items-center gap-3 font-mono">
          <span
            className={cn(
              "inline-flex items-center gap-1.5 px-2 py-0.5 rounded border",
              status === "open" && "border-emerald-500/20 text-emerald-600 dark:text-emerald-400 bg-emerald-500/10",
              status === "connecting" && "border-amber-500/20 text-amber-600 dark:text-amber-400 bg-amber-500/10",
              status === "closed" && "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
              status === "idle" && "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
              status === "error" && "border-red-500/20 text-red-600 dark:text-red-400 bg-red-500/10",
            )}
          >
            <span
              className={cn(
                "w-1.5 h-1.5 rounded-full",
                status === "open" && "bg-emerald-500 animate-pulse",
                status === "connecting" && "bg-amber-500 animate-pulse",
                status === "closed" && "bg-muted-foreground",
                status === "idle" && "bg-muted-foreground",
                status === "error" && "bg-red-500",
              )}
            />
            {status}
          </span>
          <span className="text-muted-foreground">
            {effectiveShell || "no shell"}
            {effectiveUser ? ` as ${effectiveUser}` : ""}
            {containerId ? ` · ${containerId.slice(0, 12)}` : deploymentId ? ` · ${deploymentId.slice(0, 8)}` : " · first running"}
          </span>
          {errorMsg && <span className="text-red-600 dark:text-red-400">{errorMsg}</span>}
        </div>
        <span className="text-muted-foreground">Enter to send · Ctrl+C to interrupt</span>
      </div>
      <div
        ref={scrollRef}
        className="h-[440px] overflow-y-auto bg-black text-cream/90 font-mono text-xs p-4 whitespace-pre-wrap break-all"
        onClick={() => inputRef.current?.focus()}
      >
        {output ? output : (
          <div className="text-muted-foreground">
            {status === "idle" ? "Pick an entrypoint + user, then Connect." : "Waiting for shell…"}
          </div>
        )}
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
          placeholder={status === "open" ? "Type a command…" : "Not connected"}
          className="flex-1 bg-transparent text-cream/90 font-mono text-sm focus:outline-none disabled:opacity-50"
        />
      </div>
    </div>
  );
}
