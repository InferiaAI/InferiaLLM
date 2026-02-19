import { useQuery } from "@tanstack/react-query";
import api, { computeApi } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Circle,
  Clock,
  ExternalLink,
  Rocket,
  Server,
  Shield,
  Sparkles,
  TrendingUp,
  Wrench,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { OverviewSkeleton } from "@/components/skeletons/OverviewSkeleton";
import { Link } from "react-router-dom";
import type { LucideIcon } from "lucide-react";

interface Organization {
  id: string;
  name: string;
  api_key: string | null;
  created_at: string;
}

interface DeploymentRecord {
  deployment_id: string;
  model_name?: string;
  state?: string;
  created_at?: string;
}

interface PoolRecord {
  pool_id: string;
  pool_name: string;
  provider: string;
  is_active: boolean;
}

interface PoolsResponse {
  pools: PoolRecord[];
}

interface InferenceLog {
  id: string;
  deployment_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  latency_ms?: number;
  created_at: string;
  status_code?: number;
}

interface AuditLog {
  id: string;
  action: string;
  resource_type: string;
  timestamp: string;
}

interface StatCardProps {
  label: string;
  value: number;
  status: string;
  icon: LucideIcon;
}

interface QuickActionProps {
  title: string;
  description: string;
  href: string;
  icon: LucideIcon;
}

function getStatusTone(state: string) {
  if (["RUNNING", "READY"].includes(state)) return "success";
  if (["FAILED", "ERROR"].includes(state)) return "danger";
  if (["DEPLOYING", "PENDING", "STARTING"].includes(state)) return "warning";
  return "neutral";
}

function formatState(state?: string): string {
  if (!state) return "Unknown";
  return state.charAt(0).toUpperCase() + state.slice(1).toLowerCase();
}

function StatCard({ label, value, status, icon: Icon }: StatCardProps) {
  return (
    <div className="rounded-xl border bg-card p-5 shadow-sm">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
          <p className="mt-2 text-3xl font-semibold tracking-tight">{value}</p>
        </div>
        <div className="rounded-lg border bg-background p-2">
          <Icon className="h-5 w-5 text-muted-foreground" />
        </div>
      </div>
      <div className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
        <span>{status}</span>
      </div>
    </div>
  );
}

function QuickAction({ title, description, href, icon: Icon }: QuickActionProps) {
  return (
    <Link
      to={href}
      className="group rounded-xl border bg-card p-4 shadow-sm transition-colors hover:border-blue-300 hover:bg-blue-50/30 dark:hover:bg-blue-900/10"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium">{title}</p>
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        </div>
        <div className="rounded-lg border bg-background p-2 text-muted-foreground transition-colors group-hover:text-blue-600 dark:group-hover:text-blue-400">
          <Icon className="h-4 w-4" />
        </div>
      </div>
      <div className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-blue-600 dark:text-blue-400">
        Open
        <ArrowRight className="h-3.5 w-3.5" />
      </div>
    </Link>
  );
}

export default function Overview() {
  const { user } = useAuth();

  const { data: org, isLoading: orgLoading, error: orgError } = useQuery({
    queryKey: ["organization"],
    queryFn: async () => {
      const { data } = await api.get<Organization>("/management/organizations/me");
      return data;
    },
  });

  const orgId = user?.org_id || org?.id;

  const { data: poolsData, isLoading: poolsLoading, error: poolsError } = useQuery({
    queryKey: ["pools", orgId],
    queryFn: async () => {
      if (!orgId) return { pools: [] } satisfies PoolsResponse;
      const { data } = await computeApi.get<PoolsResponse>(`/deployment/listPools/${orgId}`);
      return data;
    },
    enabled: !!orgId,
  });

  const { data: deployments, isLoading: deploymentsLoading, error: deploymentsError } = useQuery({
    queryKey: ["deployments", orgId],
    queryFn: async () => {
      if (!orgId) return [] as DeploymentRecord[];
      const { data } = await computeApi.get<{ deployments?: DeploymentRecord[] } | DeploymentRecord[]>(
        `/deployment/deployments?org_id=${orgId}`
      );
      return Array.isArray(data) ? data : (data.deployments ?? []);
    },
    enabled: !!orgId,
  });

  const { data: recentLogs, isLoading: logsLoading } = useQuery({
    queryKey: ["recent-logs", orgId],
    queryFn: async () => {
      const { data } = await api.get<InferenceLog[]>("/management/deployments/recent-logs?limit=8");
      return data;
    },
    enabled: !!orgId,
  });

  const { data: auditLogs } = useQuery({
    queryKey: ["audit-logs", orgId],
    queryFn: async () => {
      try {
        const { data } = await api.get<AuditLog[]>("/management/audit/logs?limit=5");
        return data;
      } catch {
        return [] as AuditLog[];
      }
    },
    enabled: !!orgId,
  });

  const isLoading = orgLoading || poolsLoading || deploymentsLoading || logsLoading;
  const error = orgError || poolsError || deploymentsError;

  const deploymentList = deployments ?? [];
  const poolList = poolsData?.pools ?? [];
  const runningDeployments = deploymentList.filter((d) =>
    ["RUNNING", "READY", "PENDING", "DEPLOYING"].includes((d.state || "").toUpperCase())
  );
  const healthyPools = poolList.filter((pool) => pool.is_active).length;

  if (isLoading) {
    return <OverviewSkeleton />;
  }

  if (error) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-destructive/20 bg-destructive/5 p-6 text-destructive">
        <AlertCircle className="h-5 w-5" />
        <span>Failed to load dashboard data. Please check your connection or try again.</span>
      </div>
    );
  }

  return (
    <div className="space-y-8 animate-in fade-in-50 duration-300">
      <section className="rounded-2xl border bg-card p-6 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Control Plane Overview</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {org?.name ? `${org.name} organization` : "Organization"} · signed in as {user?.email}
            </p>
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            <Link
              to="/dashboard/deployments/new"
              className="inline-flex items-center justify-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
            >
              <Rocket className="h-4 w-4" />
              Deploy Model
            </Link>
            <Link
              to="/dashboard/insights"
              className="inline-flex items-center justify-center gap-2 rounded-md border bg-background px-4 py-2 text-sm font-medium transition-colors hover:bg-muted"
            >
              <TrendingUp className="h-4 w-4" />
              Open Insights
            </Link>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Deployments"
          value={deploymentList.length}
          status={runningDeployments.length > 0 ? `${runningDeployments.length} active` : "No active deployments"}
          icon={Rocket}
        />
        <StatCard
          label="Compute Pools"
          value={poolList.length}
          status={poolList.length > 0 ? `${healthyPools} healthy` : "No pools configured"}
          icon={Server}
        />
        <StatCard label="Recent Requests" value={recentLogs?.length ?? 0} status="Latest entries" icon={Activity} />
        <StatCard
          label="Audit Events"
          value={auditLogs?.length ?? 0}
          status={auditLogs && auditLogs.length > 0 ? "Recent admin activity" : "No recent audit events"}
          icon={Shield}
        />
      </section>

      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-blue-600 dark:text-blue-400" />
          <h2 className="text-lg font-medium">Quick Setup</h2>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <QuickAction
            title="Create Deployment"
            description="Launch an inference or embedding workload."
            href="/dashboard/deployments/new"
            icon={Rocket}
          />
          <QuickAction
            title="Add Compute Pool"
            description="Provision compute capacity for model serving."
            href="/dashboard/compute/pools/new"
            icon={Server}
          />
          <QuickAction
            title="Configure Providers"
            description="Set up cloud, vector DB, and guardrail integrations."
            href="/dashboard/settings/providers"
            icon={Wrench}
          />
          <QuickAction
            title="Manage Organization"
            description="Update quota, privacy, and organization controls."
            href="/dashboard/settings/organization"
            icon={Shield}
          />
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-2">
        <div className="rounded-xl border bg-card shadow-sm">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <h3 className="text-sm font-medium">Running Deployments</h3>
            <Link to="/dashboard/deployments" className="text-xs font-medium text-blue-600 hover:underline dark:text-blue-400">
              View all
            </Link>
          </div>
          <div className="divide-y">
            {runningDeployments.length === 0 ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                <Circle className="mx-auto mb-2 h-7 w-7 opacity-25" />
                No active deployments.
              </div>
            ) : (
              runningDeployments.slice(0, 5).map((job) => {
                const state = (job.state || "unknown").toUpperCase();
                const tone = getStatusTone(state);

                return (
                  <div key={job.deployment_id} className="flex items-center justify-between px-4 py-3">
                    <div>
                      <p className="text-sm font-medium">{job.model_name || "Unnamed model"}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{formatState(state)}</p>
                    </div>
                    <Link
                      to={`/dashboard/deployments/${job.deployment_id}`}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium",
                        tone === "success" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
                        tone === "warning" && "bg-amber-500/10 text-amber-700 dark:text-amber-400",
                        tone === "danger" && "bg-red-500/10 text-red-700 dark:text-red-400",
                        tone === "neutral" && "bg-muted text-muted-foreground"
                      )}
                    >
                      Details
                      <ExternalLink className="h-3 w-3" />
                    </Link>
                  </div>
                );
              })
            )}
          </div>
        </div>

        <div className="rounded-xl border bg-card shadow-sm">
          <div className="flex items-center justify-between border-b px-4 py-3">
            <h3 className="text-sm font-medium">Compute Pool Health</h3>
            <Link to="/dashboard/compute/pools" className="text-xs font-medium text-blue-600 hover:underline dark:text-blue-400">
              Manage pools
            </Link>
          </div>
          <div className="divide-y">
            {poolList.length === 0 ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                <Server className="mx-auto mb-2 h-7 w-7 opacity-25" />
                No pools configured.
              </div>
            ) : (
              poolList.slice(0, 5).map((pool) => (
                <div key={pool.pool_id} className="flex items-center justify-between px-4 py-3">
                  <div>
                    <p className="text-sm font-medium">{pool.pool_name}</p>
                    <p className="mt-1 text-xs capitalize text-muted-foreground">{pool.provider}</p>
                  </div>
                  <span
                    className={cn(
                      "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium",
                      pool.is_active
                        ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                        : "bg-muted text-muted-foreground"
                    )}
                  >
                    {pool.is_active ? "Healthy" : "Inactive"}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <section className="rounded-xl border bg-card shadow-sm">
        <div className="flex items-center justify-between border-b px-4 py-3">
          <h3 className="text-sm font-medium">Recent Inference Activity</h3>
          <Link to="/dashboard/insights" className="text-xs font-medium text-blue-600 hover:underline dark:text-blue-400">
            Explore insights
          </Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">Timestamp</th>
                <th className="px-4 py-3 font-medium">Deployment</th>
                <th className="px-4 py-3 font-medium">Tokens</th>
                <th className="px-4 py-3 font-medium">Latency</th>
                <th className="px-4 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {recentLogs && recentLogs.length > 0 ? (
                recentLogs.map((log) => {
                  const isSuccess = !log.status_code || log.status_code < 400;
                  return (
                    <tr key={log.id} className="hover:bg-muted/30">
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                        <div className="inline-flex items-center gap-2">
                          <Clock className="h-3.5 w-3.5" />
                          {new Date(log.created_at).toLocaleString()}
                        </div>
                      </td>
                      <td className="px-4 py-3 font-medium text-foreground">{log.deployment_id.slice(0, 10)}...</td>
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                        {log.prompt_tokens}/{log.completion_tokens}
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                        {log.latency_ms ? `${log.latency_ms}ms` : "-"}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={cn(
                            "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium",
                            isSuccess
                              ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                              : "bg-red-500/10 text-red-700 dark:text-red-400"
                          )}
                        >
                          {isSuccess ? "Success" : "Error"}
                        </span>
                      </td>
                    </tr>
                  );
                })
              ) : (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-sm text-muted-foreground">
                    <div className="flex flex-col items-center gap-2">
                      <Activity className="h-7 w-7 opacity-30" />
                      No recent inference activity.
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
