import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, Link, useNavigate, Routes, Route, Navigate } from "react-router-dom";
import { ChevronRight, RefreshCw, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useAuth } from "@/context/AuthContext";
import { getNode, type NodeView } from "@/services/nodeService";
import {
  getProvisioning,
  retryProvisioning,
  type ProvisioningSummary,
} from "@/services/provisioningService";
import { AWSMetadataGrid } from "@/components/nodes/AWSMetadataGrid";
import ProvisioningStatus from "@/components/nodes/ProvisioningStatus";
import NodeShell from "@/components/nodes/NodeShell";
import NodeLogs from "@/components/nodes/NodeLogs";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
type NodeTab = "provisioning" | "ec2" | "shell" | "logs";

// ---------------------------------------------------------------------------
// NodeDetail page
// ---------------------------------------------------------------------------
export default function NodeDetail() {
  const { id: poolId, nid } = useParams<{ id: string; nid: string }>();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const canRetry = hasPermission("deployment:create");

  const [node, setNode] = useState<NodeView | null>(null);
  const [summary, setSummary] = useState<ProvisioningSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

  // Use a ref for the poll interval so polling doesn't remount shell/logs tabs
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const fetchProvisioning = useCallback(async () => {
    if (!nid) return;
    try {
      const s = await getProvisioning(nid);
      setSummary(s);
      // Stop polling once the job reaches a terminal state
      if (s.terminal) {
        stopPolling();
      }
    } catch {
      // Swallow polling errors — they're noisy and the initial load handles errors
    }
  }, [nid, stopPolling]);

  // Initial data load
  useEffect(() => {
    if (!nid) return;
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const [n, s] = await Promise.all([getNode(nid), getProvisioning(nid)]);
        if (cancelled) return;
        setNode(n);
        setSummary(s);

        // Start polling if not yet terminal
        if (!s.terminal) {
          pollRef.current = setInterval(() => void fetchProvisioning(), 3_000);
        }
      } catch (e: unknown) {
        if (cancelled) return;
        const status = (e as { response?: { status?: number } })?.response?.status;
        if (status === 404) {
          toast.error("Node not found");
          navigate(`/dashboard/compute/pools/${poolId}`, { replace: true });
        } else {
          setError("Failed to load node details.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [nid, poolId, navigate, fetchProvisioning, stopPolling]);

  const handleRetry = async () => {
    if (!nid) return;
    setRetrying(true);
    try {
      await retryProvisioning(nid);
      toast.success("Retry triggered");
      // Reset terminal flag so polling restarts
      setSummary((prev) => (prev ? { ...prev, terminal: false } : prev));
      await fetchProvisioning();
      if (pollRef.current === null) {
        pollRef.current = setInterval(() => void fetchProvisioning(), 3_000);
      }
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || "Retry failed");
    } finally {
      setRetrying(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Loading / error guards
  // ---------------------------------------------------------------------------
  if (loading) {
    return (
      <div className="p-10 text-center text-muted-foreground">Loading node…</div>
    );
  }
  if (error || !node) {
    return (
      <div className="p-10 text-center text-red-500">{error || "Node not found."}</div>
    );
  }

  const isAws = node.provider === "aws";

  // ---------------------------------------------------------------------------
  // Derive active tab from the current path suffix
  // ---------------------------------------------------------------------------
  const tabs: { label: string; value: NodeTab; hidden?: boolean }[] = [
    { label: "Provisioning Status", value: "provisioning" },
    { label: "EC2 Details", value: "ec2", hidden: !isAws },
    { label: "Shell", value: "shell" },
    { label: "Logs", value: "logs" },
  ];

  const backPath = `/dashboard/compute/pools/${poolId}`;

  return (
    <div className="min-h-screen bg-background text-foreground font-sans">
      {/* Breadcrumb */}
      <div className="flex items-center text-sm text-muted-foreground mb-6 pt-2 flex-wrap gap-1">
        <Link
          to="/dashboard/compute/pools"
          className="hover:text-foreground transition-colors"
        >
          Compute Pools
        </Link>
        <ChevronRight className="w-4 h-4 opacity-50" />
        <Link
          to={backPath}
          className="hover:text-foreground transition-colors"
        >
          {poolId?.slice(0, 8)}
        </Link>
        <ChevronRight className="w-4 h-4 opacity-50" />
        <span className="text-foreground font-medium font-mono">
          {node.node_name || node.id.slice(0, 8)}
        </span>
      </div>

      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 mb-8 border-b pb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight mb-2 font-mono">
            {node.node_name || node.id}
          </h1>
          <div className="flex items-center gap-2 text-sm text-muted-foreground flex-wrap">
            <span className="font-mono">{node.id}</span>
            <span
              className={cn(
                "px-2 py-0.5 rounded border text-xs font-medium capitalize",
                node.state === "ready"
                  ? "border-ember-500/20 text-ember-600 dark:text-ember-400 bg-ember-500/10"
                  : node.state === "terminated" || node.state === "failed"
                    ? "border-red-500/20 text-red-600 dark:text-red-400 bg-red-500/10"
                    : "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
              )}
            >
              {node.state}
            </span>
            {node.provider && (
              <span className="px-2 py-0.5 rounded border border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10 text-xs font-medium">
                {node.provider}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to={backPath}
            className="h-9 px-4 flex items-center gap-2 border rounded-md bg-background hover:bg-muted/50 transition-colors text-sm font-medium"
          >
            ← Back to pool
          </Link>
          <button
            onClick={() => void fetchProvisioning()}
            className="h-9 px-4 flex items-center gap-2 border rounded-md bg-background hover:bg-muted/50 transition-colors text-sm font-medium"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      </div>

      {/* Sub-tabs (URL-driven) */}
      <Routes>
        {/* Default redirect to provisioning */}
        <Route index element={<Navigate to="provisioning" replace />} />

        <Route
          path="provisioning"
          element={
            <NodeTabLayout tabs={tabs} activeTab="provisioning" poolId={poolId} nid={nid}>
              <div className="space-y-4">
                {summary ? (
                  <ProvisioningStatus
                    summary={summary}
                    attemptCount={summary.attempt_count}
                  />
                ) : (
                  <div className="text-muted-foreground text-sm p-4">
                    No provisioning data available.
                  </div>
                )}
                {summary?.error && canRetry && (
                  <div className="flex justify-end">
                    <button
                      data-testid="retry-provisioning-btn"
                      onClick={() => void handleRetry()}
                      disabled={retrying}
                      className={cn(
                        "h-9 px-4 flex items-center gap-2 rounded-md text-sm font-medium transition-colors border",
                        retrying
                          ? "bg-muted text-muted-foreground cursor-not-allowed"
                          : "bg-ember-600 text-white hover:bg-ember-700 border-transparent",
                      )}
                    >
                      <RotateCcw className="w-4 h-4" />
                      {retrying ? "Retrying…" : "Retry Provisioning"}
                    </button>
                  </div>
                )}
              </div>
            </NodeTabLayout>
          }
        />

        <Route
          path="ec2"
          element={
            isAws ? (
              <NodeTabLayout tabs={tabs} activeTab="ec2" poolId={poolId} nid={nid}>
                {summary?.aws_metadata ? (
                  <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-6">
                    <h3 className="font-mono text-sm font-semibold mb-4">EC2 Instance Details</h3>
                    <AWSMetadataGrid metadata={summary.aws_metadata} />
                  </div>
                ) : (
                  <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-6">
                    <p className="text-sm text-muted-foreground">
                      EC2 details not available yet. The instance may still be provisioning.
                    </p>
                  </div>
                )}
              </NodeTabLayout>
            ) : (
              <Navigate to="../provisioning" replace />
            )
          }
        />

        <Route
          path="shell"
          element={
            <NodeTabLayout tabs={tabs} activeTab="shell" poolId={poolId} nid={nid}>
              <NodeShell
                nodeId={node.id}
                nodeState={node.state}
                currentPhase={summary?.current_phase}
              />
            </NodeTabLayout>
          }
        />

        <Route
          path="logs"
          element={
            <NodeTabLayout tabs={tabs} activeTab="logs" poolId={poolId} nid={nid}>
              <NodeLogs
                nodeId={node.id}
                nodeProvider={node.provider ?? undefined}
                nodeState={node.state}
              />
            </NodeTabLayout>
          }
        />

        {/* Fallback: any unknown sub-path → provisioning */}
        <Route path="*" element={<Navigate to="provisioning" replace />} />
      </Routes>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tab layout sub-component (renders tab bar + content)
// ---------------------------------------------------------------------------
function NodeTabLayout({
  tabs,
  activeTab,
  poolId,
  nid,
  children,
}: {
  tabs: { label: string; value: NodeTab; hidden?: boolean }[];
  activeTab: NodeTab;
  poolId: string | undefined;
  nid: string | undefined;
  children: React.ReactNode;
}) {
  const base = `/dashboard/compute/pools/${poolId}/nodes/${nid}`;
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-1 mb-2">
        {tabs
          .filter((t) => !t.hidden)
          .map((t) => (
            <Link
              key={t.value}
              to={`${base}/${t.value}`}
              className={cn(
                "px-4 py-1.5 rounded-md text-sm font-medium transition-colors",
                activeTab === t.value
                  ? "bg-muted text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted/50",
              )}
            >
              {t.label}
            </Link>
          ))}
      </div>
      <div>{children}</div>
    </div>
  );
}
