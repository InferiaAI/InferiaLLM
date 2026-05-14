import { useState, useEffect, useCallback } from "react";
import { useParams, Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  RefreshCw,
  Square,
  Trash2,
  ChevronRight,
  ExternalLink,
  Plus,
  ServerCog,
  XCircle,
  Wifi,
  WifiOff,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { computeApi } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import {
  listWorkers,
  revokeWorker,
  type WorkerView,
} from "@/services/workerService";
import AddWorkerModal from "@/components/workers/AddWorkerModal";

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
  const [searchParams] = useSearchParams();
  const initialTab: "overview" | "nodes" | "workers" =
    searchParams.get("tab") === "workers"
      ? "workers"
      : searchParams.get("tab") === "nodes"
      ? "nodes"
      : "overview";
  const [activeTab, setActiveTab] = useState<"overview" | "nodes" | "workers">(
    initialTab,
  );

  const [nodes, setNodes] = useState<any[]>([]);
  const [poolDetails, setPoolDetails] = useState<PoolDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [poolId, setPoolId] = useState(id || "");
  const [isStopping, setIsStopping] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  // Workers tab state
  const [workers, setWorkers] = useState<WorkerView[]>([]);
  const [workersLoading, setWorkersLoading] = useState(false);
  const [addWorkerOpen, setAddWorkerOpen] = useState(false);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const canAddWorker = hasPermission("deployment:create");
  const canRevokeWorker = hasPermission("deployment:delete");

  const fetchWorkers = useCallback(
    async (options?: { silent?: boolean }) => {
      if (!id) return;
      if (!options?.silent) setWorkersLoading(true);
      try {
        const rows = await listWorkers(id);
        setWorkers(rows);
      } catch (e) {
        if (!options?.silent) {
          console.error("listWorkers failed:", e);
          toast.error("Failed to load workers");
        }
      } finally {
        if (!options?.silent) setWorkersLoading(false);
      }
    },
    [id],
  );

  // Initial load + 10s polling while the Workers tab is active.
  useEffect(() => {
    if (activeTab !== "workers" || !id) return;
    void fetchWorkers();
    const interval = window.setInterval(() => {
      void fetchWorkers({ silent: true });
    }, 10_000);
    return () => window.clearInterval(interval);
  }, [activeTab, id, fetchWorkers]);

  const handleRevokeWorker = useCallback(
    async (nodeId: string) => {
      if (!window.confirm("Revoke this worker? Its WS will be closed and its state marked terminated.")) {
        return;
      }
      setRevokingId(nodeId);
      try {
        await revokeWorker(nodeId);
        toast.success("Worker revoked");
        await fetchWorkers({ silent: true });
      } catch (e: unknown) {
        const detail =
          (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        toast.error(detail || "Revoke failed");
      } finally {
        setRevokingId(null);
      }
    },
    [fetchWorkers],
  );

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
    return <div className="p-10 text-center text-muted-foreground">Loading pool details...</div>;
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
                    ? "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10"
                    : "border-ember-500/20 text-ember-600 dark:text-ember-400 bg-ember-500/10",
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
          { label: "Workers", value: "workers" as const },
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
              <h3 className="font-mono text-sm font-semibold mb-4 text-foreground dark:text-cream">Pool Information</h3>
              <div className="grid grid-cols-2 lg:grid-cols-5 gap-6">
                <div>
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Total Nodes</div>
                  <div className="text-2xl font-bold text-fg-secondary dark:text-cream/85">{nodes.length}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Active Nodes</div>
                  <div className="text-2xl font-bold text-ember-600 dark:text-ember-400">{nodes.filter(n => n.state === "active" || n.state === "ready").length}</div>
                </div>

                {poolDetails && (
                  <>
                    <div>
                      <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Provider</div>
                      <div className="text-sm font-medium capitalize bg-muted dark:bg-card px-2 py-1 rounded inline-flex mt-1 text-fg-secondary dark:text-cream/85">
                        {poolDetails.provider}
                      </div>
                    </div>
                    <div>
                      <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Allowed GPUs</div>
                      <div className="flex flex-wrap gap-1 mt-1">
                        {poolDetails.allowed_gpu_types?.length > 0 ? (
                          poolDetails.allowed_gpu_types.map((gpu: string) => (
                            <span key={gpu} className="text-xs font-mono bg-ember-50 text-ember-700 dark:bg-ember-900/30 dark:text-ember-300 px-1.5 py-0.5 rounded border border-ember-100 dark:border-ember-800">
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
                      <div className="text-sm font-medium mt-1 text-fg-secondary dark:text-cream/85">
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
                        <td className="px-6 py-4 font-mono text-ember-600 truncate max-w-[120px]" title={node.node_id}>{node.node_id}</td>
                        <td className="px-6 py-4">
                          <span className={cn(
                            "inline-flex items-center gap-1.5 px-2.5 py-1 rounded border text-xs font-medium shadow-sm",
                            node.state === "active" || node.state === "ready"
                              ? "border-ember-500/20 text-ember-600 dark:text-ember-400 bg-ember-500/10"
                              : "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10"
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
                              className="text-ember-600 hover:text-ember-800 flex items-center gap-1 font-mono text-xs truncate max-w-[200px]"
                              title={node.expose_url}
                            >
                              <ExternalLink className="w-3 h-3 flex-shrink-0" />
                              {node.expose_url}
                            </a>
                          ) : (
                            <span className="text-muted-foreground font-mono text-xs">-</span>
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

        {activeTab === "workers" && (
          <div className="border rounded-xl bg-card overflow-hidden shadow-sm">
            <div className="px-6 py-4 border-b flex items-center justify-between">
              <div>
                <h3 className="font-mono text-sm font-semibold text-foreground dark:text-cream">
                  Workers ({workers.length})
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Direct-managed GPU hosts running inferia-worker. Connection
                  state refreshes every 10s.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  className="h-8 px-3 inline-flex items-center gap-2 border rounded-md hover:bg-muted text-xs font-medium"
                  onClick={() => fetchWorkers()}
                  disabled={workersLoading}
                >
                  <RefreshCw
                    className={cn("w-3.5 h-3.5", workersLoading && "animate-spin")}
                  />
                  Refresh
                </button>
                {canAddWorker && (
                  <button
                    onClick={() => setAddWorkerOpen(true)}
                    className="h-8 px-3 inline-flex items-center gap-2 bg-ember-600 hover:bg-ember-700 text-white rounded-md text-xs font-medium"
                  >
                    <Plus className="w-3.5 h-3.5" /> Add Worker
                  </button>
                )}
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-xs text-muted-foreground uppercase tracking-wider">
                  <tr>
                    <th className="px-6 py-3 text-left font-mono">Node</th>
                    <th className="px-6 py-3 text-left font-mono">Connection</th>
                    <th className="px-6 py-3 text-left font-mono">State</th>
                    <th className="px-6 py-3 text-left font-mono">CPU%</th>
                    <th className="px-6 py-3 text-left font-mono">Loaded Models</th>
                    <th className="px-6 py-3 text-left font-mono">Advertise URL</th>
                    <th className="px-6 py-3 text-left font-mono">Last Heartbeat</th>
                    <th className="px-6 py-3 text-right font-mono">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {workers.length === 0 ? (
                    <tr>
                      <td
                        colSpan={8}
                        className="px-6 py-12 text-center text-muted-foreground"
                      >
                        <div className="flex flex-col items-center gap-2">
                          <ServerCog className="w-6 h-6 opacity-50" />
                          <div>No workers in this pool yet.</div>
                          {canAddWorker && (
                            <button
                              onClick={() => setAddWorkerOpen(true)}
                              className="mt-2 text-xs text-ember-600 hover:text-ember-700 underline"
                            >
                              Add the first one
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ) : (
                    workers.map((w) => (
                      <tr
                        key={w.node_id}
                        className="bg-background hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors"
                      >
                        <td
                          className="px-6 py-4 font-mono text-ember-600 truncate max-w-[180px]"
                          title={`${w.node_name || w.node_id}\n${w.node_id}`}
                        >
                          <div className="font-medium">{w.node_name || w.node_id}</div>
                          <div className="text-xs text-muted-foreground truncate">
                            {w.node_id}
                          </div>
                        </td>
                        <td className="px-6 py-4">
                          {w.connected ? (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 bg-emerald-500/10">
                              <Wifi className="w-3 h-3" /> Live
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium border border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10">
                              <WifiOff className="w-3 h-3" /> Offline
                            </span>
                          )}
                        </td>
                        <td className="px-6 py-4">
                          <span
                            className={cn(
                              "inline-flex items-center px-2 py-0.5 rounded text-xs font-medium",
                              w.state === "ready"
                                ? "border border-ember-500/20 text-ember-600 dark:text-ember-400 bg-ember-500/10"
                                : w.state === "terminated"
                                ? "border border-red-500/20 text-red-600 dark:text-red-400 bg-red-500/10"
                                : "border border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
                            )}
                          >
                            {w.state}
                          </span>
                        </td>
                        <td className="px-6 py-4 font-mono text-xs">
                          {w.used.cpu_pct ?? "-"}
                        </td>
                        <td className="px-6 py-4 font-mono text-xs">
                          {w.loaded_models.length}
                        </td>
                        <td className="px-6 py-4">
                          {w.advertise_url ? (
                            <a
                              href={w.advertise_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-ember-600 hover:text-ember-800 flex items-center gap-1 font-mono text-xs truncate max-w-[200px]"
                              title={w.advertise_url}
                            >
                              <ExternalLink className="w-3 h-3 flex-shrink-0" />
                              {w.advertise_url}
                            </a>
                          ) : (
                            <span className="text-muted-foreground font-mono text-xs">
                              -
                            </span>
                          )}
                        </td>
                        <td className="px-6 py-4 font-mono text-xs text-muted-foreground">
                          {w.last_heartbeat
                            ? new Date(w.last_heartbeat).toLocaleString()
                            : "-"}
                        </td>
                        <td className="px-6 py-4 text-right">
                          {canRevokeWorker && w.state !== "terminated" && (
                            <button
                              onClick={() => handleRevokeWorker(w.node_id)}
                              disabled={revokingId === w.node_id}
                              className={cn(
                                "inline-flex items-center gap-1 px-2 py-1 rounded text-xs",
                                revokingId === w.node_id
                                  ? "text-muted-foreground cursor-wait"
                                  : "text-red-600 hover:text-red-700 hover:bg-red-500/10",
                              )}
                              title="Revoke worker"
                            >
                              <XCircle className="w-3.5 h-3.5" />
                              Revoke
                            </button>
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

      {addWorkerOpen && (
        <AddWorkerModal
          poolId={poolId || id || ""}
          poolName={poolDetails?.pool_name}
          onClose={() => {
            setAddWorkerOpen(false);
            void fetchWorkers({ silent: true });
          }}
        />
      )}
    </div>
  );
}
