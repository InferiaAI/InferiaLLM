import { useState, useEffect, useCallback, useRef } from "react";
import {
  useParams,
  Link,
  useNavigate,
  Routes,
  Route,
  useLocation,
} from "react-router-dom";
import { RefreshCw, ChevronRight, ScrollText, Terminal, Activity, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { getPool, deletePool, type PoolView } from "@/services/poolService";
import { listNodes, deleteNode, type NodeView } from "@/services/nodeService";
import { computeApi } from "@/lib/api";
import NodeDetail from "./NodeDetail";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type Tab = "overview" | "nodes" | "deployments" | "settings";

interface DeploymentRow {
  deployment_id: string;
  model_name?: string;
  state?: string;
  status?: string;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function PoolDetail() {
  const location = useLocation();

  // If the current URL is inside a node sub-route, render NodeDetail as full
  // content-area takeover so deep-links and refreshes work regardless of
  // activeTab. Pattern: /compute/pools/:id/nodes/:nid/...
  // This branch is handled in a thin wrapper so the data-driven body below can
  // call its hooks unconditionally (Rules of Hooks).
  const isNodeSubRoute = /\/nodes\/[^/]+/.test(location.pathname);
  if (isNodeSubRoute) {
    return (
      <Routes>
        <Route path="nodes/:nid/*" element={<NodeDetail />} />
      </Routes>
    );
  }
  return <PoolDetailContent />;
}

function PoolDetailContent() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const canDelete = hasPermission("deployment:delete");

  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [pool, setPool] = useState<PoolView | null>(null);
  const [nodes, setNodes] = useState<NodeView[]>([]);
  const [deployments, setDeployments] = useState<DeploymentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);

  // Tracks whether the last fetchNodes call errored, so we only toast on an
  // error transition (not on every 15s poll while the backend stays down).
  const nodesErroredRef = useRef(false);

  // ---------------------------------------------------------------------------
  // Fetch
  // ---------------------------------------------------------------------------
  const fetchPool = useCallback(
    async (silent = false) => {
      if (!id) return;
      if (!silent) setLoading(true);
      try {
        const p = await getPool(id);
        setPool(p);
      } catch (e: unknown) {
        const status = (e as { response?: { status?: number } })?.response
          ?.status;
        if (status === 404) {
          toast.error("Pool not found");
          navigate("/dashboard/compute/pools", { replace: true });
          return;
        }
        if (!silent) toast.error("Failed to load pool");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [id, navigate],
  );

  const fetchNodes = useCallback(async () => {
    if (!id) return;
    try {
      const allNodes = await listNodes();
      setNodes(allNodes.filter((n) => n.pool_id === id));
      // Successful fetch (including a legitimately empty pool) clears the error
      // latch so a future failure toasts again.
      nodesErroredRef.current = false;
    } catch {
      // Only toast on the transition into an errored state to avoid spamming
      // a notification on every 15s poll while the backend stays unreachable.
      if (!nodesErroredRef.current) {
        nodesErroredRef.current = true;
        toast.error("Failed to load nodes");
      }
    }
  }, [id]);

  const fetchDeployments = useCallback(async () => {
    if (!id) return;
    try {
      const res = await computeApi.get<{ deployments?: DeploymentRow[] }>(
        `/deployment/list?pool_id=${id}`,
      );
      setDeployments(res.data?.deployments || []);
    } catch {
      /* swallow */
    }
  }, [id]);

  useEffect(() => {
    void fetchPool();
    void fetchNodes();
    void fetchDeployments();
    const interval = window.setInterval(() => {
      void fetchPool(true);
      void fetchNodes();
    }, 15_000);
    return () => window.clearInterval(interval);
  }, [fetchPool, fetchNodes, fetchDeployments]);

  // ---------------------------------------------------------------------------
  // Delete
  // ---------------------------------------------------------------------------
  const handleDelete = async () => {
    if (!id || !pool) return;
    if (
      !window.confirm(
        `Delete pool '${pool.pool_name}'? This destroys every node currently in the pool. Active deployments must be stopped first.`,
      )
    )
      return;
    setDeleting(true);
    try {
      await deletePool(id);
      toast.success("Pool deleted");
      navigate("/dashboard/compute/pools", { replace: true });
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response
        ?.status;
      const detail = (
        e as { response?: { data?: { detail?: string } } }
      )?.response?.data?.detail;
      if (status === 409) {
        alert(detail || "Cannot delete pool: active deployments must be stopped first.");
      } else {
        toast.error(detail || "Failed to delete pool");
      }
    } finally {
      setDeleting(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Render guards
  // ---------------------------------------------------------------------------
  if (loading) {
    return (
      <div className="p-10 text-center text-muted-foreground">
        Loading pool…
      </div>
    );
  }
  if (!pool) return null;

  const nodeCount = nodes.length;
  const gpuFree = nodes.reduce(
    (s, n) => s + ((n.gpu_total ?? 0) - (n.gpu_allocated ?? 0)),
    0,
  );
  const gpuTotal = nodes.reduce((s, n) => s + (n.gpu_total ?? 0), 0);

  const tabs: { label: string; value: Tab }[] = [
    { label: "Overview", value: "overview" },
    { label: "Nodes", value: "nodes" },
    { label: "Deployments", value: "deployments" },
    { label: "Settings", value: "settings" },
  ];

  return (
    <div className="min-h-screen bg-background text-foreground font-sans">
      {/* Breadcrumb */}
      <div className="flex items-center text-sm text-muted-foreground mb-6 pt-2">
        <Link
          to="/dashboard/compute/pools"
          className="hover:text-foreground transition-colors"
        >
          Compute Pools
        </Link>
        <ChevronRight className="w-4 h-4 mx-2 opacity-50" />
        <span className="text-foreground font-medium">
          {pool.pool_name || pool.pool_id.slice(0, 8)}
        </span>
      </div>

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-6 mb-8 border-b pb-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight mb-2">
            {pool.pool_name || "Unnamed Pool"}
          </h1>
          <div className="flex items-center gap-2 text-sm text-muted-foreground font-mono flex-wrap">
            <span>ID:</span>
            <span className="px-2 py-0.5 rounded border bg-muted/50 text-foreground">
              {pool.pool_id}
            </span>
            <span
              className={cn(
                "px-2 py-0.5 rounded border text-xs font-medium capitalize",
                pool.lifecycle_state === "active" ||
                  pool.lifecycle_state === "ready"
                  ? "border-ember-500/20 text-ember-600 dark:text-ember-400 bg-ember-500/10"
                  : pool.lifecycle_state === "terminated" ||
                      pool.lifecycle_state === "failed"
                    ? "border-red-500/20 text-red-600 dark:text-red-400 bg-red-500/10"
                    : "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
              )}
            >
              {pool.lifecycle_state || "unknown"}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void fetchPool()}
            className="h-9 px-4 flex items-center gap-2 border rounded-md bg-background hover:bg-muted/50 transition-colors text-sm font-medium"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 mb-6">
        {tabs.map((t) => (
          <button
            key={t.value}
            onClick={() => setActiveTab(t.value)}
            className={cn(
              "px-4 py-1.5 rounded-md text-sm font-medium transition-colors",
              activeTab === t.value
                ? "bg-muted text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="space-y-6">
        {/* ----------------------------------------------------------------- */}
        {/* Overview tab                                                       */}
        {/* ----------------------------------------------------------------- */}
        {activeTab === "overview" && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <StatCard label="Nodes" value={String(nodeCount)} />
            <StatCard
              label="GPU Free / Total"
              value={`${gpuFree} / ${gpuTotal}`}
            />
            <StatCard label="Deployments" value={String(deployments.length)} />
            <div className="sm:col-span-3 rounded-xl border bg-card text-card-foreground shadow-sm p-6">
              <h3 className="font-mono text-sm font-semibold mb-4">
                Pool Information
              </h3>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-6 text-sm">
                <InfoField label="Provider" value={pool.provider || "—"} />
                <InfoField label="Type" value={pool.pool_type || "—"} />
                <InfoField
                  label="GPU Count"
                  value={String(pool.gpu_count ?? "—")}
                />
                <InfoField
                  label="Max Cost/hr"
                  value={
                    pool.max_cost_per_hour
                      ? `$${pool.max_cost_per_hour.toFixed(2)}`
                      : "—"
                  }
                />
                <InfoField
                  label="GPU Types"
                  value={(pool.allowed_gpu_types || []).join(", ") || "—"}
                />
                <InfoField
                  label="Dedicated"
                  value={pool.is_dedicated ? "Yes" : "No"}
                />
                <InfoField
                  label="Created"
                  value={
                    pool.created_at
                      ? new Date(pool.created_at).toLocaleString()
                      : "—"
                  }
                />
                <InfoField
                  label="Updated"
                  value={
                    pool.updated_at
                      ? new Date(pool.updated_at).toLocaleString()
                      : "—"
                  }
                />
              </div>
            </div>
          </div>
        )}

        {/* ----------------------------------------------------------------- */}
        {/* Nodes tab                                                          */}
        {/* ----------------------------------------------------------------- */}
        {activeTab === "nodes" && (
          <div className="space-y-4">
            <NodeList
              nodes={nodes}
              poolId={id ?? ""}
              canDelete={canDelete}
              onRefetch={fetchNodes}
            />
          </div>
        )}

        {/* ----------------------------------------------------------------- */}
        {/* Deployments tab                                                    */}
        {/* ----------------------------------------------------------------- */}
        {activeTab === "deployments" && (
          <div className="rounded-xl border bg-card overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-xs text-muted-foreground uppercase tracking-wider">
                  <tr>
                    <th className="px-6 py-3 text-left font-mono">
                      Deployment
                    </th>
                    <th className="px-6 py-3 text-left font-mono">Model</th>
                    <th className="px-6 py-3 text-left font-mono">State</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {deployments.length === 0 ? (
                    <tr>
                      <td
                        colSpan={3}
                        className="px-6 py-8 text-center text-muted-foreground"
                      >
                        No deployments on this pool.
                      </td>
                    </tr>
                  ) : (
                    deployments.map((d) => {
                      const dState = (d.state || d.status || "").toUpperCase();
                      return (
                        <tr
                          key={d.deployment_id}
                          className="bg-background hover:bg-muted/50 transition-colors"
                        >
                          <td className="px-6 py-4">
                            <Link
                              to={`/dashboard/deployments/${d.deployment_id}`}
                              className="font-mono text-ember-600 hover:text-ember-700 text-xs"
                            >
                              {d.deployment_id.slice(0, 8)}
                            </Link>
                          </td>
                          <td className="px-6 py-4 text-xs font-mono">
                            {d.model_name || "—"}
                          </td>
                          <td className="px-6 py-4">
                            <span
                              className={cn(
                                "inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium",
                                dState === "RUNNING" || dState === "ACTIVE"
                                  ? "border-ember-500/20 text-ember-600 bg-ember-500/10"
                                  : dState === "FAILED" ||
                                      dState === "TERMINATED"
                                    ? "border-red-500/20 text-red-600 bg-red-500/10"
                                    : "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
                              )}
                            >
                              {dState || "—"}
                            </span>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ----------------------------------------------------------------- */}
        {/* Settings tab                                                       */}
        {/* ----------------------------------------------------------------- */}
        {activeTab === "settings" && (
          <div className="space-y-6">
            <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-6 space-y-4">
              <h3 className="font-mono text-sm font-semibold">Pool Settings</h3>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <InfoField
                  label="Max nodes"
                  value={
                    (pool as unknown as { max_nodes?: number | null })
                      ?.max_nodes != null
                      ? String(
                          (pool as unknown as { max_nodes?: number }).max_nodes,
                        )
                      : "unlimited"
                  }
                />
                <InfoField
                  label="$/hr cap"
                  value={
                    pool.max_cost_per_hour
                      ? `$${pool.max_cost_per_hour.toFixed(2)}`
                      : "—"
                  }
                />
              </div>
            </div>

            {canDelete && (
              <div className="rounded-xl border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/20 p-6">
                <h3 className="font-semibold text-sm text-red-800 dark:text-red-300 mb-2">
                  Danger Zone
                </h3>
                <p className="text-sm text-red-700 dark:text-red-400 mb-4">
                  Deleting this pool destroys every node currently in it.
                  Active deployments must be stopped first.
                </p>
                <button
                  onClick={handleDelete}
                  disabled={deleting}
                  className={cn(
                    "px-4 py-2 rounded-md text-sm font-medium transition-colors",
                    deleting
                      ? "bg-muted text-muted-foreground cursor-not-allowed"
                      : "bg-red-600 text-white hover:bg-red-700",
                  )}
                >
                  {deleting ? "Deleting…" : "Delete Pool"}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper sub-components
// ---------------------------------------------------------------------------

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-5">
      <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">
        {label}
      </div>
      <div className="text-2xl font-bold">{value}</div>
    </div>
  );
}

function InfoField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">
        {label}
      </div>
      <div className="font-mono text-sm">{value}</div>
    </div>
  );
}

function NodeList({
  nodes,
  poolId,
  canDelete,
  onRefetch,
}: {
  nodes: NodeView[];
  poolId: string;
  canDelete: boolean;
  onRefetch: () => void | Promise<void>;
}) {
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const handleDeleteNode = async (nodeId: string) => {
    if (
      !window.confirm(
        "Delete this node? For AWS this terminates the EC2 instance.",
      )
    )
      return;
    setDeletingId(nodeId);
    try {
      const res = await deleteNode(nodeId);
      toast.success(
        res.terminating
          ? "Termination started — destroying the EC2 instance…"
          : "Node deleted",
      );
      await onRefetch();
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      const detail = (e as { response?: { data?: { detail?: string } } })?.response
        ?.data?.detail;
      if (status === 409) {
        toast.error(
          detail || "Cannot delete: stop active deployments on this node first.",
        );
      } else {
        toast.error(detail || "Failed to delete node");
      }
    } finally {
      setDeletingId(null);
    }
  };

  if (nodes.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        No nodes in this pool yet.
      </div>
    );
  }
  return (
    <div className="rounded-xl border bg-card overflow-hidden shadow-sm">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-xs text-muted-foreground uppercase tracking-wider">
          <tr>
            <th className="px-6 py-3 text-left font-mono">Node</th>
            <th className="px-6 py-3 text-left font-mono">State</th>
            <th className="px-6 py-3 text-left font-mono">GPU</th>
            <th className="px-6 py-3 text-left font-mono">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {nodes.map((n) => {
            const detailBase = `/dashboard/compute/pools/${poolId}/nodes/${n.id}`;
            return (
              <tr
                key={n.id}
                className="bg-background hover:bg-muted/50 transition-colors cursor-pointer"
                onClick={() => {
                  window.location.href = `${detailBase}/provisioning`;
                }}
              >
                <td className="px-6 py-4">
                  <Link
                    to={`${detailBase}/provisioning`}
                    onClick={(e) => e.stopPropagation()}
                    className="font-mono text-xs text-ember-600 hover:text-ember-700"
                  >
                    {n.node_name || n.id.slice(0, 8)}
                  </Link>
                  <div className="text-xs text-muted-foreground">{n.id}</div>
                </td>
                <td className="px-6 py-4">
                  <span
                    className={cn(
                      "inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium",
                      n.state === "ready"
                        ? "border-ember-500/20 text-ember-600 bg-ember-500/10"
                        : n.state === "terminated"
                          ? "border-red-500/20 text-red-600 bg-red-500/10"
                          : "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
                    )}
                  >
                    {n.state}
                  </span>
                </td>
                <td className="px-6 py-4 font-mono text-xs">
                  {n.gpu_allocated ?? 0}/{n.gpu_total ?? 0}
                </td>
                <td className="px-6 py-4">
                  <div className="flex items-center gap-2">
                    <Link
                      to={`${detailBase}/provisioning`}
                      onClick={(e) => e.stopPropagation()}
                      className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    >
                      <Activity className="w-3.5 h-3.5" /> Status
                    </Link>
                    <Link
                      to={`${detailBase}/shell`}
                      onClick={(e) => e.stopPropagation()}
                      className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    >
                      <Terminal className="w-3.5 h-3.5" /> Shell
                    </Link>
                    <Link
                      to={`${detailBase}/logs`}
                      onClick={(e) => e.stopPropagation()}
                      className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    >
                      <ScrollText className="w-3.5 h-3.5" /> Logs
                    </Link>
                    {canDelete && (
                      <button
                        data-testid={`delete-node-${n.id}`}
                        disabled={deletingId === n.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleDeleteNode(n.id);
                        }}
                        className={cn(
                          "inline-flex items-center gap-1 text-xs",
                          deletingId === n.id
                            ? "text-muted-foreground cursor-not-allowed"
                            : "text-red-600 hover:text-red-700",
                        )}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        {deletingId === n.id ? "Deleting…" : "Delete"}
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

