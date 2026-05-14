import { useState, useEffect, useCallback, useMemo } from "react";
import { Play, RefreshCw, Search, Wifi, WifiOff, X } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { listNodes, type NodeView } from "@/services/nodeService";

export default function Instances() {
  const { hasPermission } = useAuth();
  const canCreate = hasPermission("deployment:create");
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState("");
  const [labelFilters, setLabelFilters] = useState<Array<{ k: string; v: string }>>([]);
  const [draftLabel, setDraftLabel] = useState("");
  const [nodes, setNodes] = useState<NodeView[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const selector = useMemo(() => {
    if (labelFilters.length === 0) return undefined;
    const out: Record<string, string> = {};
    for (const { k, v } of labelFilters) out[k] = v;
    return out;
  }, [labelFilters]);

  const fetchNodes = useCallback(async (silent = false) => {
    if (!silent) setIsLoading(true);
    try {
      const rows = await listNodes(selector);
      setNodes(rows);
    } catch (e) {
      console.error("listNodes failed", e);
      if (!silent) toast.error("Failed to load nodes");
    } finally {
      if (!silent) setIsLoading(false);
      setRefreshing(false);
    }
  }, [selector]);

  useEffect(() => {
    void fetchNodes();
    const interval = window.setInterval(() => void fetchNodes(true), 15_000);
    return () => window.clearInterval(interval);
  }, [fetchNodes]);

  const handleRefresh = () => {
    setRefreshing(true);
    void fetchNodes();
  };

  const addLabelFilter = () => {
    const trimmed = draftLabel.trim();
    if (!trimmed.includes("=")) {
      toast.error("Filter must be key=value");
      return;
    }
    const [k, v] = trimmed.split("=", 2);
    if (!k || labelFilters.some((f) => f.k === k && f.v === v)) {
      setDraftLabel("");
      return;
    }
    setLabelFilters([...labelFilters, { k: k.trim(), v: v.trim() }]);
    setDraftLabel("");
  };

  const removeLabelFilter = (idx: number) => {
    setLabelFilters(labelFilters.filter((_, i) => i !== idx));
  };

  const filteredNodes = nodes.filter((n) => {
    const haystack = [n.node_name, n.id, n.provider, n.agent_kind]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(search.toLowerCase());
  });

  return (
    <div className="space-y-4 font-sans text-foreground dark:text-cream">
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-bold tracking-tight">Compute Nodes</h1>
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            <button
              className="h-9 px-3 flex items-center gap-2 border rounded-md bg-card dark:border-border hover:bg-muted dark:hover:bg-card transition-colors text-sm font-medium text-fg-secondary dark:text-cream/85 shadow-sm"
              onClick={handleRefresh}
            >
              <RefreshCw className={cn("w-3.5 h-3.5", refreshing && "animate-spin")} />
              Refresh
            </button>
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <input
                placeholder="Search nodes…"
                className="h-9 w-56 rounded-md border dark:border-border bg-card pl-9 pr-4 text-sm outline-none focus:ring-1 focus:ring-ember-500 shadow-sm placeholder:text-muted-foreground dark:text-cream/85"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <div className="flex items-center gap-2">
              <input
                placeholder="label key=value"
                className="h-9 w-56 rounded-md border bg-card px-3 text-sm outline-none focus:ring-1 focus:ring-ember-500"
                value={draftLabel}
                onChange={(e) => setDraftLabel(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addLabelFilter();
                  }
                }}
              />
              <button
                onClick={addLabelFilter}
                className="h-9 px-3 text-sm rounded-md border hover:bg-muted"
              >
                + Filter
              </button>
            </div>
            {labelFilters.length > 0 && (
              <div className="flex items-center gap-1 flex-wrap">
                {labelFilters.map((f, i) => (
                  <span
                    key={`${f.k}=${f.v}`}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded border bg-muted/30 text-xs font-mono"
                  >
                    {f.k}={f.v}
                    <button
                      onClick={() => removeLabelFilter(i)}
                      className="opacity-60 hover:opacity-100"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {canCreate && (
            <Link
              to="/dashboard/compute/nodes/new"
              className="h-9 px-4 bg-ember-600 text-white rounded-md text-sm font-medium hover:bg-ember-700 transition-colors shadow-sm inline-flex items-center gap-2"
            >
              <Play className="w-4 h-4" />
              Add Node
            </Link>
          )}
        </div>
      </div>

      <div className="border rounded-xl bg-card overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground uppercase tracking-wider">
              <tr>
                <th className="px-6 py-3 text-left font-mono">Name</th>
                <th className="px-6 py-3 text-left font-mono">Provider</th>
                <th className="px-6 py-3 text-left font-mono">State</th>
                <th className="px-6 py-3 text-left font-mono">GPU</th>
                <th className="px-6 py-3 text-left font-mono">Labels</th>
                <th className="px-6 py-3 text-left font-mono">Last Heartbeat</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {isLoading && filteredNodes.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-8 text-center text-muted-foreground">
                    Loading…
                  </td>
                </tr>
              ) : filteredNodes.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-muted-foreground">
                    <div className="flex flex-col items-center gap-2">
                      <div>No nodes yet.</div>
                      {canCreate && (
                        <Link
                          to="/dashboard/compute/nodes/new"
                          className="text-xs text-ember-600 hover:text-ember-700 underline"
                        >
                          Add your first node
                        </Link>
                      )}
                    </div>
                  </td>
                </tr>
              ) : (
                filteredNodes.map((n) => (
                  <tr
                    key={n.id}
                    className="bg-background hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors"
                  >
                    <td className="px-6 py-4">
                      <Link
                        to={`/dashboard/compute/nodes/${n.id}`}
                        className="font-mono text-ember-600 hover:text-ember-700"
                      >
                        {n.node_name || n.id.slice(0, 8)}
                      </Link>
                      <div className="text-xs text-muted-foreground truncate max-w-[280px]">
                        {n.id}
                      </div>
                    </td>
                    <td className="px-6 py-4 font-mono text-xs">
                      <div>{n.provider || "—"}</div>
                      <div className="text-muted-foreground">{n.agent_kind || ""}</div>
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={cn(
                          "inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-xs font-medium",
                          n.state === "ready"
                            ? "border-ember-500/20 text-ember-600 dark:text-ember-400 bg-ember-500/10"
                            : n.state === "terminated"
                            ? "border-red-500/20 text-red-600 dark:text-red-400 bg-red-500/10"
                            : "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
                        )}
                      >
                        {n.state}
                      </span>
                      {n.agent_kind === "worker" && (
                        <div className="mt-1 text-xs inline-flex items-center gap-1 text-muted-foreground">
                          {n.last_heartbeat &&
                          Date.now() - new Date(n.last_heartbeat).getTime() < 15_000 ? (
                            <>
                              <Wifi className="w-3 h-3 text-emerald-500" /> live
                            </>
                          ) : (
                            <>
                              <WifiOff className="w-3 h-3" /> offline
                            </>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="px-6 py-4 font-mono text-xs">
                      {n.gpu_allocated ?? 0}/{n.gpu_total ?? 0}
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex flex-wrap gap-1 max-w-[280px]">
                        {Object.entries(n.labels || {}).slice(0, 4).map(([k, v]) => (
                          <span
                            key={k}
                            className="text-xs font-mono bg-muted/40 px-1.5 py-0.5 rounded border"
                          >
                            {k}={v}
                          </span>
                        ))}
                        {Object.keys(n.labels || {}).length > 4 && (
                          <span className="text-xs text-muted-foreground">
                            +{Object.keys(n.labels || {}).length - 4} more
                          </span>
                        )}
                        {Object.keys(n.labels || {}).length === 0 && (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 font-mono text-xs text-muted-foreground">
                      {n.last_heartbeat
                        ? new Date(n.last_heartbeat).toLocaleString()
                        : "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
