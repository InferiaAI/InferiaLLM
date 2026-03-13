import { useState, useEffect, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import {
  RefreshCw,
  Square,
  Trash2,
  ChevronRight,
  ExternalLink,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { computeApi } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

type PoolLifecycleState = "running" | "terminating" | "terminated";

type PoolDetails = {
  pool_id: string;
  pool_name: string;
  provider: string;
  allowed_gpu_types?: string[];
  max_cost_per_hour?: number;
  lifecycle_state?: PoolLifecycleState;
};

export default function InstanceDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const canStopPool =
    hasPermission("deployment:update") || hasPermission("deployment:delete");
  const canDeletePool = hasPermission("deployment:delete");
  const [activeTab, setActiveTab] = useState<"overview" | "nodes">("overview");

  const [nodes, setNodes] = useState<any[]>([]);
  const [poolDetails, setPoolDetails] = useState<PoolDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [poolId, setPoolId] = useState(id || "");
  const [isStopping, setIsStopping] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const fetchInventory = useCallback(
    async (options?: { silent?: boolean }) => {
      if (!id) {
        return;
      }

      if (!options?.silent) {
        setLoading(true);
      }

      try {
        const [inventoryRes, poolRes] = await Promise.all([
          computeApi.get(`/deployment/list/pool/${id}/inventory`),
          computeApi.get(`/deployment/pool/${id}`).catch(() => null),
        ]);
        const data = inventoryRes.data;
        setNodes(data.nodes || []);
        setPoolId(data.pool_id);
        if (poolRes?.data) {
          setPoolDetails(poolRes.data);
        }
      } catch (error) {
        if (!options?.silent) {
          toast.error("Failed to fetch pool details");
        }
        console.error(error);
      } finally {
        if (!options?.silent) {
          setLoading(false);
        }
      }
    },
    [id],
  );

  // Fetch pool inventory and details
  useEffect(() => {
    if (id) {
      void fetchInventory();
    }
  }, [id, fetchInventory]);

  useEffect(() => {
    if (!id || poolDetails?.lifecycle_state !== "terminating") {
      return;
    }

    const interval = setInterval(() => {
      void fetchInventory({ silent: true });
    }, 5000);
    return () => clearInterval(interval);
  }, [id, poolDetails?.lifecycle_state, fetchInventory]);

  if (loading) {
    return <div className="p-10 text-center text-slate-500">Loading pool details...</div>;
  }

  const lifecycleState = (poolDetails?.lifecycle_state || "running") as PoolLifecycleState;
  const isTerminating = lifecycleState === "terminating";
  const isTerminated = lifecycleState === "terminated";
  const poolName = poolDetails?.pool_name || "Compute Pool";
  const stopButtonDisabled = !id || isStopping || isTerminating || isTerminated;
  const deleteButtonDisabled = !id || isDeleting || !isTerminated;

  const handleStop = async () => {
    if (!id) return;
    if (!canStopPool) {
      toast.error("You don't have permission to stop pools");
      return;
    }
    if (isTerminated) {
      toast.info("Pool is already terminated");
      return;
    }
    if (isTerminating) {
      toast.info("Pool termination is already in progress");
      return;
    }
    if (!confirm("Stop this pool now? It will enter terminating state before it can be deleted.")) {
      return;
    }

    setIsStopping(true);
    try {
      await computeApi.post(`/deployment/stoppool/${id}`);
      toast.success("Pool is terminating");
      await fetchInventory();
    } catch (error) {
      toast.error("Failed to stop pool");
      console.error(error);
    } finally {
      setIsStopping(false);
    }
  };

  const handleDelete = async () => {
    if (!id) return;
    if (!canDeletePool) {
      toast.error("You don't have permission to delete pools");
      return;
    }
    if (!isTerminated) {
      toast.error("Stop the pool and wait for terminated state before deleting");
      return;
    }

    if (!confirm("Are you sure you want to delete this pool? This action cannot be undone.")) return;

    setIsDeleting(true);
    try {
      await computeApi.post(`/deployment/deletepool/${id}`);
      toast.success("Pool deleted successfully");
      navigate("/dashboard/compute/pools");
    } catch (error) {
      toast.error("Failed to delete pool");
      console.error(error);
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div className="min-h-screen bg-background text-foreground font-sans">
      {/* Top Bar (Breadcrumbs & Actions) */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6 pt-2">
        <div className="flex items-center text-sm text-muted-foreground">
          <Link
            to="/dashboard/compute/pools"
            className="hover:text-foreground transition-colors"
          >
            Pools
          </Link>
          <ChevronRight className="w-4 h-4 mx-2 opacity-50" />
          <span className="text-foreground font-medium capitalize">
            {poolName}
          </span>
          <ChevronRight className="w-4 h-4 mx-2 opacity-50" />
          <span className="font-mono text-foreground font-medium">
            {poolId}
          </span>
        </div>
      </div>

      {/* Title & Main Actions */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-6 mb-8 border-b pb-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight mb-2">
            {poolName}
          </h1>
          <div className="flex items-center gap-2 text-sm text-muted-foreground font-mono">
            <span>ID:</span>
            <span className="px-2 py-0.5 rounded border bg-muted/50 text-foreground">
              {poolId}
            </span>
            <span
              className={cn(
                "px-2 py-0.5 rounded border text-xs font-medium capitalize",
                isTerminating
                  ? "border-amber-500/20 text-amber-600 dark:text-amber-400 bg-amber-500/10"
                  : isTerminated
                    ? "border-slate-500/20 text-slate-600 dark:text-slate-400 bg-slate-500/10"
                    : "border-emerald-500/20 text-emerald-600 dark:text-emerald-400 bg-emerald-500/10",
              )}
            >
              {isTerminating ? "Terminating" : isTerminated ? "Terminated" : "Running"}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => void fetchInventory()}
            className="h-9 px-4 flex items-center gap-2 border rounded-md bg-background hover:bg-muted/50 transition-colors text-sm font-medium"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
          {canStopPool && (
            <button
              onClick={handleStop}
              disabled={stopButtonDisabled}
              className={cn(
                "h-9 px-4 flex items-center gap-2 border rounded-md transition-colors text-sm font-medium shadow-sm",
                stopButtonDisabled
                  ? "bg-muted text-muted-foreground cursor-not-allowed"
                  : "bg-amber-500 text-white border-amber-500 hover:bg-amber-600",
              )}
            >
              <Square className={cn("w-4 h-4", (isStopping || isTerminating) && "animate-pulse")} />
              {isTerminating ? "Terminating..." : "Stop Pool"}
            </button>
          )}
          {canDeletePool && (
            <button
              onClick={handleDelete}
              disabled={deleteButtonDisabled}
              title={isTerminated ? "Delete Pool" : "Pool must be terminated before deletion"}
              className={cn(
                "h-9 px-4 flex items-center gap-2 border rounded-md transition-colors text-sm font-medium shadow-sm",
                deleteButtonDisabled
                  ? "bg-muted text-muted-foreground cursor-not-allowed"
                  : "bg-destructive text-destructive-foreground hover:bg-destructive/90",
              )}
            >
              <Trash2 className="w-4 h-4" /> Delete Pool
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 mb-6">
        {[
          { label: "Overview", value: "overview" as const },
          { label: "Nodes", value: "nodes" as const },
        ].map(
          (tab) => (
            <button
              key={tab.value}
              onClick={() => setActiveTab(tab.value)}
              className={cn(
                "px-4 py-1.5 rounded-md text-sm font-medium transition-colors",
                activeTab === tab.value
                  ? "bg-muted text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/50",
              )}
            >
              {tab.label}
            </button>
          ),
        )}
      </div>

      {/* Tab Content */}
      <div className="space-y-6">
        {activeTab === "overview" && (
          <div className="grid grid-cols-1 gap-6">
            <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-6">
              <h3 className="font-mono text-sm font-semibold mb-4 text-slate-900 dark:text-zinc-100">Pool Information</h3>
              <div className="grid grid-cols-2 lg:grid-cols-5 gap-6">
                <div>
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Total Nodes</div>
                  <div className="text-2xl font-bold text-slate-800 dark:text-zinc-200">{nodes.length}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Active Nodes</div>
                  <div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">{nodes.filter(n => n.state === "active" || n.state === "ready").length}</div>
                </div>

                {poolDetails && (
                  <>
                    <div>
                      <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Provider</div>
                      <div className="text-sm font-medium capitalize bg-slate-100 dark:bg-zinc-800 px-2 py-1 rounded inline-flex mt-1 text-slate-800 dark:text-zinc-200">
                        {poolDetails.provider}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Allowed GPUs</div>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {poolDetails.allowed_gpu_types?.length > 0 ? (
                          poolDetails.allowed_gpu_types.map((gpu: string) => (
                            <span key={gpu} className="text-xs font-mono bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 px-1.5 py-0.5 rounded border border-emerald-100 dark:border-emerald-800">
                              {gpu}
                            </span>
                          ))
                        ) : (
                          <span className="text-sm text-muted-foreground">Any</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Max Cost</div>
                      <div className="text-sm font-medium mt-1 text-slate-800 dark:text-zinc-200">
                        {poolDetails.max_cost_per_hour > 0
                          ? `$${poolDetails.max_cost_per_hour.toFixed(2)} / hr`
                          : "Uncapped"}
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        )}

        {activeTab === "nodes" && (
          <div className="border rounded-xl bg-card overflow-hidden shadow-sm">
            <div className="px-6 py-4 border-b">
              <h3 className="font-medium text-foreground">
                Nodes ({nodes.length})
              </h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead className="bg-muted/50 text-muted-foreground border-b dark:bg-muted/20">
                  <tr>
                    <th className="px-6 py-3 text-left font-mono">Node ID</th>
                    <th className="px-6 py-3 text-left font-mono">State</th>
                    <th className="px-6 py-3 text-left font-mono">Provider</th>
                    <th className="px-6 py-3 text-left font-mono">GPU (Alloc/Total)</th>
                    <th className="px-6 py-3 text-left font-mono">vCPU (Alloc/Total)</th>
                    <th className="px-6 py-3 text-left font-mono">Service URL</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {nodes.length === 0 ? (
                    <tr><td colSpan={6} className="px-6 py-8 text-center text-muted-foreground">No nodes provisioned in this pool yet.</td></tr>
                  ) : (
                    nodes.map((node) => (
                      <tr key={node.node_id} className="bg-background hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
                        <td className="px-6 py-4 font-mono text-emerald-600 truncate max-w-[120px]" title={node.node_id}>{node.node_id}</td>
                        <td className="px-6 py-4">
                          <span className={cn(
                            "inline-flex items-center gap-1.5 px-2.5 py-1 rounded border text-xs font-medium shadow-sm",
                            node.state === "active" || node.state === "ready"
                              ? "border-emerald-500/20 text-emerald-600 dark:text-emerald-400 bg-emerald-500/10"
                              : "border-slate-500/20 text-slate-600 dark:text-slate-400 bg-slate-500/10"
                          )}>
                            {node.state}
                          </span>
                        </td>
                        <td className="px-6 py-4 font-mono">{node.provider}</td>
                        <td className="px-6 py-4 font-mono">{node.gpu_allocated} / {node.gpu_total}</td>
                        <td className="px-6 py-4 font-mono">{node.vcpu_allocated} / {node.vcpu_total}</td>
                        <td className="px-6 py-4">
                          {node.expose_url ? (
                            <a
                              href={node.expose_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-emerald-600 hover:text-emerald-800 flex items-center gap-1 font-mono text-xs truncate max-w-[200px]"
                              title={node.expose_url}
                            >
                              <ExternalLink className="w-3 h-3 flex-shrink-0" />
                              {node.expose_url}
                            </a>
                          ) : (
                            <span className="text-slate-400 font-mono text-xs">-</span>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Placeholder Logs/Events tabs if needed */}
      </div>
    </div>
  );
}
