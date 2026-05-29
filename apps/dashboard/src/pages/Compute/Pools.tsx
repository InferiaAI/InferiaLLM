import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Search } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { listPools, type PoolView } from "@/services/poolService";

export default function Pools() {
  const { hasPermission, user, organizations } = useAuth();
  const canCreate = hasPermission("deployment:create");
  const navigate = useNavigate();

  const [pools, setPools] = useState<PoolView[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState("");

  const orgId = user?.org_id || organizations?.[0]?.id || "";

  const fetchPools = useCallback(
    async (silent = false) => {
      if (!orgId) return;
      if (!silent) setIsLoading(true);
      try {
        const rows = await listPools(orgId);
        setPools(rows);
      } catch (e) {
        console.error("listPools failed", e);
        if (!silent) toast.error("Failed to load pools");
      } finally {
        if (!silent) setIsLoading(false);
        setRefreshing(false);
      }
    },
    [orgId],
  );

  useEffect(() => {
    void fetchPools();
    const interval = window.setInterval(() => void fetchPools(true), 15_000);
    return () => window.clearInterval(interval);
  }, [fetchPools]);

  const handleRefresh = () => {
    setRefreshing(true);
    void fetchPools();
  };

  const filteredPools = pools.filter((p) => {
    const haystack = [p.pool_name, p.pool_id, p.provider, p.pool_type]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(search.toLowerCase());
  });

  return (
    <div className="space-y-4 font-sans text-foreground dark:text-cream">
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-bold tracking-tight">Compute Pools</h1>
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            <button
              className="h-9 px-3 flex items-center gap-2 border rounded-md bg-card dark:border-border hover:bg-muted dark:hover:bg-card transition-colors text-sm font-medium text-fg-secondary dark:text-cream/85 shadow-sm"
              onClick={handleRefresh}
            >
              <RefreshCw
                className={cn("w-3.5 h-3.5", refreshing && "animate-spin")}
              />
              Refresh
            </button>
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <input
                placeholder="Search pools…"
                className="h-9 w-56 rounded-md border dark:border-border bg-card pl-9 pr-4 text-sm outline-none focus:ring-1 focus:ring-ember-500 shadow-sm placeholder:text-muted-foreground dark:text-cream/85"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          {canCreate && (
            <Link
              to="/dashboard/compute/pools/new"
              className="h-9 px-4 bg-ember-600 text-white rounded-md text-sm font-medium hover:bg-ember-700 transition-colors shadow-sm inline-flex items-center gap-2"
            >
              + New Pool
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
                <th className="px-6 py-3 text-left font-mono">Type</th>
                <th className="px-6 py-3 text-left font-mono">GPU Count</th>
                <th className="px-6 py-3 text-left font-mono">GPU Types</th>
                <th className="px-6 py-3 text-left font-mono">State</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {isLoading && filteredPools.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-6 py-8 text-center text-muted-foreground"
                  >
                    Loading…
                  </td>
                </tr>
              ) : filteredPools.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-6 py-12 text-center text-muted-foreground"
                  >
                    <div className="flex flex-col items-center gap-2">
                      <div>No pools yet.</div>
                      {canCreate && (
                        <Link
                          to="/dashboard/compute/pools/new"
                          className="text-xs text-ember-600 hover:text-ember-700 underline"
                        >
                          Create your first pool
                        </Link>
                      )}
                    </div>
                  </td>
                </tr>
              ) : (
                filteredPools.map((p) => (
                  <tr
                    key={p.pool_id}
                    className="bg-background hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors cursor-pointer"
                    onClick={() =>
                      navigate(`/dashboard/compute/pools/${p.pool_id}`)
                    }
                  >
                    <td className="px-6 py-4">
                      <div className="font-mono text-ember-600 hover:text-ember-700">
                        {p.pool_name || p.pool_id.slice(0, 8)}
                      </div>
                      <div className="text-xs text-muted-foreground truncate max-w-[280px]">
                        {p.pool_id}
                      </div>
                    </td>
                    <td className="px-6 py-4 font-mono text-xs">
                      {p.provider || "—"}
                    </td>
                    <td className="px-6 py-4 font-mono text-xs">
                      {p.pool_type || "—"}
                    </td>
                    <td className="px-6 py-4 font-mono text-xs">
                      {p.gpu_count ?? "—"}
                    </td>
                    <td className="px-6 py-4">
                      <div className="flex flex-wrap gap-1 max-w-[200px]">
                        {(p.allowed_gpu_types || []).slice(0, 3).map((g) => (
                          <span
                            key={g}
                            className="text-xs font-mono bg-muted/40 px-1.5 py-0.5 rounded border"
                          >
                            {g}
                          </span>
                        ))}
                        {(p.allowed_gpu_types || []).length > 3 && (
                          <span className="text-xs text-muted-foreground">
                            +{(p.allowed_gpu_types || []).length - 3} more
                          </span>
                        )}
                        {(p.allowed_gpu_types || []).length === 0 && (
                          <span className="text-xs text-muted-foreground">
                            —
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <span
                        className={cn(
                          "inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-xs font-medium",
                          p.lifecycle_state === "active" ||
                            p.lifecycle_state === "ready"
                            ? "border-ember-500/20 text-ember-600 dark:text-ember-400 bg-ember-500/10"
                            : p.lifecycle_state === "terminated" ||
                                p.lifecycle_state === "failed"
                              ? "border-red-500/20 text-red-600 dark:text-red-400 bg-red-500/10"
                              : "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
                        )}
                      >
                        {p.lifecycle_state || "unknown"}
                      </span>
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
