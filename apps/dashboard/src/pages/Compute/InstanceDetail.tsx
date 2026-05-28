import { useState, useEffect, useCallback } from "react";
import { useParams, Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  RefreshCw,
  Trash2,
  ChevronRight,
  ExternalLink,
  Wifi,
  WifiOff,
  Tag,
  Terminal,
  ScrollText,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import {
  getNode,
  patchLabels,
  deleteNode,
  type NodeView,
} from "@/services/nodeService";
import LabelEditor from "@/components/nodes/LabelEditor";
import NodeLogs from "@/components/nodes/NodeLogs";
import NodeShell from "@/components/nodes/NodeShell";
import ProvisioningStatus from "@/components/nodes/ProvisioningStatus";
import { AWSMetadataGrid } from "@/components/nodes/AWSMetadataGrid";
import { RetryProvisioningButton } from "@/components/nodes/RetryProvisioningButton";
import { getProvisioning, type ProvisioningSummary } from "@/services/provisioningService";

type Tab = "overview" | "labels" | "logs" | "shell";

function parseTab(s: string | null): Tab {
  if (s === "labels" || s === "logs" || s === "shell")
    return s;
  return "overview";
}

export default function InstanceDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const canEdit = hasPermission("deployment:update");
  const canDelete = hasPermission("deployment:delete");

  const [searchParams] = useSearchParams();
  const initialTab: Tab = parseTab(searchParams.get("tab"));
  const [activeTab, setActiveTab] = useState<Tab>(initialTab);

  const [node, setNode] = useState<NodeView | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [provisioning, setProvisioning] = useState<ProvisioningSummary | null>(null);

  const fetchNode = useCallback(async (silent = false) => {
    if (!id) return;
    if (!silent) setLoading(true);
    try {
      const row = await getNode(id);
      setNode(row);
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        toast.error("Node not found");
        navigate("/dashboard/compute/nodes", { replace: true });
        return;
      }
      console.error(e);
      if (!silent) toast.error("Failed to load node");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [id, navigate]);

  // Initial load — fires once per id change (fetchNode identity changes when id changes).
  useEffect(() => {
    void fetchNode();
  }, [fetchNode]);

  // Adaptive polling interval — re-creates the interval when state changes.
  useEffect(() => {
    const period =
      (node?.state === "provisioning" || node?.state === "ordered") ? 2000 : 15000;
    const interval = window.setInterval(() => void fetchNode(true), period);
    return () => window.clearInterval(interval);
  }, [fetchNode, node?.state]);

  useEffect(() => {
    if (!id || !node || node.provider !== "aws") return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await getProvisioning(id);
        if (!cancelled) setProvisioning(r);
      } catch { /* swallow */ }
    };
    void tick();
    const h = window.setInterval(() => void tick(), 2000);
    return () => { cancelled = true; window.clearInterval(h); };
  }, [id, node?.provider]);

  const handleAddLabel = async (k: string, v: string) => {
    if (!id) return;
    const updated = await patchLabels(id, { add: { [k]: v }, remove: [] });
    setNode(updated);
  };

  const handleRemoveLabel = async (k: string) => {
    if (!id) return;
    const updated = await patchLabels(id, { add: {}, remove: [k] });
    setNode(updated);
  };

  const handleDelete = async () => {
    if (!id || !node) return;
    const isAwsNode = node.provider === "aws";
    const prompt = isAwsNode
      ? `Delete node ${node.node_name || id}? This terminates the EC2 instance and stops billing. Takes up to 90 seconds.`
      : `Delete node ${node.node_name || id}? The row will be marked terminated.`;
    if (!window.confirm(prompt)) {
      return;
    }
    setDeleting(true);
    try {
      const result = await deleteNode(id);
      toast.success(
        result.terminating
          ? "Termination started. EC2 destroy may take up to 90 seconds."
          : "Node deleted",
      );
      navigate("/dashboard/compute/nodes", { replace: true });
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || "Failed to delete node");
    } finally {
      setDeleting(false);
    }
  };

  if (loading) {
    return <div className="p-10 text-center text-muted-foreground">Loading node…</div>;
  }
  if (!node) return null;

  const isWorker = node.agent_kind === "worker";
  const isAws = node.provider === "aws";
  const isProvisioning = node.state === "provisioning";
  const showLogsAndShell = isWorker || isAws;
  const isConnected =
    isWorker &&
    node.last_heartbeat &&
    Date.now() - new Date(node.last_heartbeat).getTime() < 15_000;

  return (
    <div className="min-h-screen bg-background text-foreground font-sans">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6 pt-2">
        <div className="flex items-center text-sm text-muted-foreground">
          <Link to="/dashboard/compute/nodes" className="hover:text-foreground transition-colors">
            Nodes
          </Link>
          <ChevronRight className="w-4 h-4 mx-2 opacity-50" />
          <span className="text-foreground font-medium">{node.node_name || node.id.slice(0, 8)}</span>
        </div>
      </div>

      <div className="flex flex-col md:flex-row md:items-end justify-between gap-6 mb-8 border-b pb-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight mb-2">
            {node.node_name || "Unnamed node"}
          </h1>
          <div className="flex items-center gap-2 text-sm text-muted-foreground font-mono flex-wrap">
            <span>ID:</span>
            <span className="px-2 py-0.5 rounded border bg-muted/50 text-foreground">{node.id}</span>
            <span
              className={cn(
                "px-2 py-0.5 rounded border text-xs font-medium capitalize",
                node.state === "ready"
                  ? "border-ember-500/20 text-ember-600 dark:text-ember-400 bg-ember-500/10"
                  : node.state === "terminated"
                  ? "border-red-500/20 text-red-600 dark:text-red-400 bg-red-500/10"
                  : "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
              )}
            >
              {node.state}
            </span>
            {isWorker && (
              <span
                className={cn(
                  "px-2 py-0.5 rounded border text-xs font-medium inline-flex items-center gap-1",
                  isConnected
                    ? "border-emerald-500/20 text-emerald-600 dark:text-emerald-400 bg-emerald-500/10"
                    : "border-muted-foreground/20 text-muted-foreground bg-muted-foreground/10",
                )}
              >
                {isConnected ? (
                  <>
                    <Wifi className="w-3 h-3" /> Live
                  </>
                ) : (
                  <>
                    <WifiOff className="w-3 h-3" /> Offline
                  </>
                )}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => void fetchNode()}
            className="h-9 px-4 flex items-center gap-2 border rounded-md bg-background hover:bg-muted/50 transition-colors text-sm font-medium"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
          {canDelete && node.state !== "terminated" && (
            <button
              onClick={handleDelete}
              disabled={deleting}
              className={cn(
                "h-9 px-4 flex items-center gap-2 border rounded-md transition-colors text-sm font-medium shadow-sm",
                deleting
                  ? "bg-muted text-muted-foreground cursor-not-allowed"
                  : "bg-destructive text-destructive-foreground hover:bg-destructive/90",
              )}
            >
              <Trash2 className="w-4 h-4" /> Delete Node
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1 mb-6">
        {([
          { label: "Overview", value: "overview" as const, icon: null },
          { label: "Labels", value: "labels" as const, icon: Tag },
          // Logs + Shell are shown for workers (advertise_url + inference_token
          // set during /v1/nodes/add/worker) and AWS nodes (provisioning logs
          // and shell surface available via the orchestration service).
          ...(showLogsAndShell
            ? [
                { label: "Logs", value: "logs" as const, icon: ScrollText },
                { label: "Shell", value: "shell" as const, icon: Terminal },
              ]
            : []),
        ] as { label: string; value: Tab; icon: React.ComponentType<{ className?: string }> | null }[]).map((t) => (
          <button
            key={t.value}
            onClick={() => setActiveTab(t.value)}
            className={cn(
              "px-4 py-1.5 rounded-md text-sm font-medium transition-colors inline-flex items-center gap-2",
              activeTab === t.value
                ? "bg-muted text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50",
            )}
          >
            {t.icon && <t.icon className="w-4 h-4" />}
            {t.label}
          </button>
        ))}
      </div>

      <div className="space-y-6">
        {activeTab === "overview" && (
          <div className="grid grid-cols-1 gap-6">
            {isAws && provisioning &&
              (isProvisioning || provisioning.phases.some(p => p.status === "failed")) && (
              <ProvisioningStatus
                summary={provisioning}
                attemptCount={provisioning.attempt_count ?? 0}
              />
            )}
            {isAws && provisioning?.error && id && (
              <div
                className="rounded-lg border border-red-400 bg-red-50 dark:bg-red-950/30 p-4"
                role="alert"
              >
                <h3 className="font-medium text-red-900 dark:text-red-200">
                  {provisioning.error.message ?? "Provisioning failed"}
                </h3>
                {provisioning.error.hint && (
                  <p className="text-sm text-red-700 dark:text-red-300 mt-1">
                    {provisioning.error.hint}
                  </p>
                )}
                <div className="mt-3">
                  <RetryProvisioningButton nodeId={id} />
                </div>
              </div>
            )}
            {isAws && provisioning?.aws_metadata && (
              <AWSMetadataGrid metadata={provisioning.aws_metadata} />
            )}
            <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-6">
              <h3 className="font-mono text-sm font-semibold mb-4">Node Information</h3>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-6 text-sm">
                <div>
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Provider</div>
                  <div className="font-mono">{node.provider || "—"}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Agent Kind</div>
                  <div className="font-mono">{node.agent_kind || "—"}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">GPU (alloc/total)</div>
                  <div className="font-mono">{node.gpu_allocated ?? 0}/{node.gpu_total ?? 0}</div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">CPU (alloc/total)</div>
                  <div className="font-mono">{node.vcpu_allocated ?? 0}/{node.vcpu_total ?? 0}</div>
                </div>
                <div className="col-span-2">
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Advertise URL</div>
                  {node.advertise_url ? (
                    <a
                      href={node.advertise_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-ember-600 hover:text-ember-700 inline-flex items-center gap-1 font-mono text-xs break-all"
                    >
                      <ExternalLink className="w-3 h-3" /> {node.advertise_url}
                    </a>
                  ) : (
                    <span className="text-muted-foreground font-mono text-xs">—</span>
                  )}
                </div>
                <div className="col-span-2">
                  <div className="text-xs text-muted-foreground uppercase tracking-wider mb-1">Last Heartbeat</div>
                  <div className="font-mono text-xs">
                    {node.last_heartbeat ? new Date(node.last_heartbeat).toLocaleString() : "—"}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {activeTab === "labels" && (
          <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="font-mono text-sm font-semibold inline-flex items-center gap-2">
                  <Tag className="w-4 h-4" /> Labels
                </h3>
                <p className="text-xs text-muted-foreground mt-1">
                  Free-form key=value pairs. Use these in deployment selectors and to group nodes
                  by role, hardware, zone, etc.
                </p>
              </div>
            </div>
            <LabelEditor
              labels={node.labels || {}}
              onAdd={handleAddLabel}
              onRemove={handleRemoveLabel}
              disabled={!canEdit || node.state === "terminated"}
            />
          </div>
        )}

        {activeTab === "logs" && showLogsAndShell && (
          <div className="space-y-3">
            <div className="text-xs text-muted-foreground">
              Live tail of the most recent model container managed by this worker. If
              no deployment is loaded yet, the stream waits for one to start.
            </div>
            <NodeLogs nodeId={node.id} nodeProvider={node.provider ?? undefined} nodeState={node.state} />
          </div>
        )}

        {activeTab === "shell" && showLogsAndShell && (
          <div className="space-y-3">
            <div className="text-xs text-muted-foreground">
              Interactive shell inside the most recent model container. Type
              commands and press <kbd className="px-1 py-0.5 rounded border bg-muted text-[10px]">Enter</kbd>;
              press <kbd className="px-1 py-0.5 rounded border bg-muted text-[10px]">Ctrl</kbd>+
              <kbd className="px-1 py-0.5 rounded border bg-muted text-[10px]">C</kbd> to interrupt.
            </div>
            <NodeShell
              nodeId={node.id}
              nodeState={node.state}
              currentPhase={provisioning?.current_phase ?? null}
            />
          </div>
        )}

      </div>
    </div>
  );
}
