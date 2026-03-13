import { useQuery } from "@tanstack/react-query";
import api, { computeApi } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { auditService } from "@/services/auditService";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Circle,
  Clock,
  ExternalLink,
  Lock,
  Rocket,
  Server,
  Shield,
  Sparkles,
  TriangleAlert,
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
  lifecycle_state?: string;
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

type StatTone = "positive" | "warning" | "danger" | "neutral" | "locked";

interface StatCardProps {
  label: string;
  value: number;
  status: string;
  icon: LucideIcon;
  tone: StatTone;
}

interface QuickActionProps {
  title: string;
  description: string;
  href: string;
  icon: LucideIcon;
  colorClass?: string;
  bgClass?: string;
}

function getStatusTone(state: string): StatTone {
  if (["RUNNING", "READY"].includes(state)) return "positive";
  if (["FAILED", "ERROR"].includes(state)) return "danger";
  if (["DEPLOYING", "PENDING", "STARTING"].includes(state)) return "warning";
  return "neutral";
}

function formatState(state?: string): string {
  if (!state) return "Unknown";
  return state.charAt(0).toUpperCase() + state.slice(1).toLowerCase();
}

function getStatToneConfig(tone: StatTone) {
  switch (tone) {
    case "positive":
      return {
        iconWrap: "bg-emerald-500/12",
        iconColor: "text-emerald-600 dark:text-emerald-400",
        statusColor: "text-emerald-700 dark:text-emerald-400",
        statusIcon: CheckCircle2,
      };
    case "warning":
      return {
        iconWrap: "bg-amber-500/12",
        iconColor: "text-amber-600 dark:text-amber-400",
        statusColor: "text-amber-700 dark:text-amber-400",
        statusIcon: TriangleAlert,
      };
    case "danger":
      return {
        iconWrap: "bg-red-500/12",
        iconColor: "text-red-600 dark:text-red-400",
        statusColor: "text-red-700 dark:text-red-400",
        statusIcon: AlertCircle,
      };
    case "locked":
      return {
        iconWrap: "bg-slate-500/12",
        iconColor: "text-slate-600 dark:text-slate-300",
        statusColor: "text-slate-600 dark:text-slate-300",
        statusIcon: Lock,
      };
    case "neutral":
    default:
      return {
        iconWrap: "bg-slate-500/10",
        iconColor: "text-slate-600 dark:text-slate-300",
        statusColor: "text-muted-foreground",
        statusIcon: Circle,
      };
  }
}

function StatCard({ label, value, status, icon: Icon, tone }: StatCardProps) {
  const toneConfig = getStatToneConfig(tone);
  const StatusIcon = toneConfig.statusIcon;

  return (
    <div className="rounded-2xl border border-border/70 bg-card p-6 shadow-sm transition-colors hover:border-border">
      <div className={cn("mb-4 inline-flex rounded-xl p-2.5", toneConfig.iconWrap)}>
        <Icon className={cn("h-5 w-5", toneConfig.iconColor)} />
      </div>

      <p className="text-sm font-medium tracking-wide text-foreground">{label}</p>
      <p className="mt-2 text-3xl font-semibold tracking-tight text-foreground">{value}</p>

      <div className={cn("mt-4 flex items-center gap-1.5 border-t border-border/60 pt-4 text-xs", toneConfig.statusColor)}>
        <StatusIcon className="h-3.5 w-3.5" />
        <span>{status}</span>
      </div>
    </div>
  );
}

function QuickAction({
  title,
  description,
  href,
  icon: Icon,
  colorClass = "text-emerald-600 dark:text-emerald-400",
  bgClass = "bg-emerald-500/10",
}: QuickActionProps) {
  return (
    <Link
      to={href}
      className="group flex h-full flex-col rounded-2xl border border-border/70 bg-card p-5 shadow-sm transition-colors hover:border-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
    >
      <div className={cn("mb-4 inline-flex rounded-xl p-2.5", bgClass)}>
        <Icon className={cn("h-5 w-5", colorClass)} />
      </div>
      <div className="flex-1">
        <p className="text-base font-semibold text-foreground">{title}</p>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{description}</p>
      </div>
      <div className="mt-4 inline-flex items-center gap-1 text-xs font-semibold text-primary">
        Open
        <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
      </div>
    </Link>
  );
}

export default function Overview() {
  const { user, hasPermission } = useAuth();
  const canViewDeployments = hasPermission("deployment:list");
  const canViewAuditLogs = hasPermission("audit_log:list");
  const canCreateDeployment = hasPermission("deployment:create");
  const canManageProviders = hasPermission("organization:update");

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
    enabled: !!orgId && canViewDeployments,
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
    enabled: !!orgId && canViewDeployments,
  });

  const { data: recentLogs, isLoading: logsLoading } = useQuery({
    queryKey: ["recent-logs", orgId],
    queryFn: async () => {
      const { data } = await api.get<InferenceLog[]>("/management/deployments/recent-logs?limit=8");
      return data;
    },
    enabled: !!orgId && canViewDeployments,
  });

  const { data: auditLogs } = useQuery({
    queryKey: ["audit-logs", orgId],
    queryFn: async () => {
      try {
        const data = await auditService.getLogs(undefined, { limit: 5 });
        return data;
      } catch {
        return [];
      }
    },
    enabled: !!orgId && canViewAuditLogs,
  });

  const isLoading = orgLoading || (canViewDeployments && (poolsLoading || deploymentsLoading || logsLoading));
  const error = orgError || (canViewDeployments ? poolsError || deploymentsError : null);

  const deploymentList = deployments ?? [];
  const poolList = poolsData?.pools ?? [];
  const runningDeployments = deploymentList.filter((d) =>
    ["RUNNING", "READY", "PENDING", "DEPLOYING"].includes((d.state || "").toUpperCase())
  );
  const isPoolHealthy = (pool: PoolRecord) => pool.is_active && !["terminated", "terminating"].includes((pool.lifecycle_state || "").toLowerCase());
  const healthyPools = poolList.filter(isPoolHealthy).length;
  const requestCount = recentLogs?.length ?? 0;
  const recentTokenCount = (recentLogs ?? []).reduce(
    (sum, log) => sum + log.prompt_tokens + log.completion_tokens,
    0
  );

  const deploymentTone: StatTone = !canViewDeployments
    ? "locked"
    : runningDeployments.length > 0
      ? "positive"
      : "neutral";

  let poolTone: StatTone = "neutral";
  if (!canViewDeployments) {
    poolTone = "locked";
  } else if (poolList.length > 0 && healthyPools === poolList.length) {
    poolTone = "positive";
  } else if (poolList.length > 0 && healthyPools === 0) {
    poolTone = "danger";
  } else if (poolList.length > 0) {
    poolTone = "warning";
  }

  const requestTone: StatTone = !canViewDeployments ? "locked" : requestCount > 0 ? "positive" : "neutral";
  const auditTone: StatTone = !canViewAuditLogs ? "locked" : (auditLogs?.length ?? 0) > 0 ? "positive" : "neutral";

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
    <div className="animate-in fade-in-50 space-y-8 duration-300">
      <section className="relative overflow-hidden rounded-2xl border border-border/70 bg-card p-6 shadow-sm">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_right,_hsl(var(--primary)/0.16),_transparent_48%)]" />
        <div className="relative grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.16em] text-primary">
              <Sparkles className="h-3.5 w-3.5" />
              Control Plane
            </div>
            <h1 className="mt-4 text-3xl font-semibold tracking-tight text-foreground">{org?.name || "Organization"} Overview</h1>
            <p className="mt-3 max-w-2xl text-sm text-muted-foreground sm:text-base">
              Operate deployments, monitor compute readiness, and keep team activity visible from one dashboard surface.
            </p>

            <div className="mt-6 flex flex-wrap gap-3">
              {canCreateDeployment && (
                <Link
                  to="/dashboard/deployments/new"
                  className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-lg shadow-primary/25 transition hover:brightness-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                >
                  Launch deployment
                  <ArrowRight className="h-4 w-4" />
                </Link>
              )}
              {canViewDeployments && (
                <Link
                  to="/dashboard/insights"
                  className="inline-flex items-center gap-2 rounded-xl border border-border/70 bg-background/70 px-4 py-2.5 text-sm font-semibold text-foreground transition hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
                >
                  View insights
                </Link>
              )}
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
            <div className="rounded-xl border border-border/70 bg-background/70 p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-muted-foreground">Active deployments</p>
              <p className="mt-2 text-2xl font-semibold text-foreground">{canViewDeployments ? runningDeployments.length : 0}</p>
            </div>
            <div className="rounded-xl border border-border/70 bg-background/70 p-4">
              <p className="text-xs uppercase tracking-[0.14em] text-muted-foreground">Pool health</p>
              <p className="mt-2 text-2xl font-semibold text-foreground">
                {canViewDeployments ? `${healthyPools}/${poolList.length || 0}` : "0/0"}
              </p>
            </div>
            <div className="rounded-xl border border-border/70 bg-background/70 p-4 sm:col-span-2 xl:col-span-1">
              <p className="text-xs uppercase tracking-[0.14em] text-muted-foreground">Recent tokens (8 req)</p>
              <p className="mt-2 text-2xl font-semibold text-foreground">{canViewDeployments ? recentTokenCount : 0}</p>
            </div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Deployments"
          value={deploymentList.length}
          status={
            canViewDeployments
              ? runningDeployments.length > 0
                ? `${runningDeployments.length} active`
                : "No active deployments"
              : "No access"
          }
          icon={Rocket}
          tone={deploymentTone}
        />
        <StatCard
          label="Compute Pools"
          value={poolList.length}
          status={
            canViewDeployments
              ? poolList.length > 0
                ? `${healthyPools} healthy`
                : "No pools configured"
              : "No access"
          }
          icon={Server}
          tone={poolTone}
        />
        <StatCard
          label="Recent Requests"
          value={requestCount}
          status={canViewDeployments ? "Latest entries" : "No access"}
          icon={Activity}
          tone={requestTone}
        />
        <StatCard
          label="Audit Events"
          value={auditLogs?.length ?? 0}
          status={
            canViewAuditLogs
              ? auditLogs && auditLogs.length > 0
                ? "Recent admin activity"
                : "No recent audit events"
              : "No access"
          }
          icon={Shield}
          tone={auditTone}
        />
      </section>

      <section className="space-y-4">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" />
          <h2 className="text-lg font-semibold">Quick Setup</h2>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {canCreateDeployment && (
            <>
              <QuickAction
                title="Create Deployment"
                description="Launch an inference or embedding workload."
                href="/dashboard/deployments/new"
                icon={Rocket}
                colorClass="text-emerald-600 dark:text-emerald-400"
                bgClass="bg-emerald-500/10"
              />
              <QuickAction
                title="Add Compute Pool"
                description="Provision compute capacity for model serving."
                href="/dashboard/compute/pools/new"
                icon={Server}
                colorClass="text-cyan-600 dark:text-cyan-400"
                bgClass="bg-cyan-500/10"
              />
            </>
          )}
          {canManageProviders && (
            <QuickAction
              title="Configure Providers"
              description="Set up cloud, vector DB, and guardrail integrations."
              href="/dashboard/settings/providers"
              icon={Wrench}
              colorClass="text-amber-600 dark:text-amber-400"
              bgClass="bg-amber-500/10"
            />
          )}
          {hasPermission("organization:view") && (
            <QuickAction
              title="Manage Organization"
              description="Update quota, privacy, and organization controls."
              href="/dashboard/settings/organization"
              icon={Shield}
              colorClass="text-blue-600 dark:text-blue-400"
              bgClass="bg-blue-500/10"
            />
          )}
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-2">
        <div className="rounded-2xl border border-border/70 bg-card shadow-sm">
          <div className="flex items-center justify-between border-b border-border/70 px-4 py-3">
            <h3 className="text-sm font-semibold">Running Deployments</h3>
            {canViewDeployments ? (
              <Link
                to="/dashboard/deployments"
                className="text-xs font-semibold text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                View all
              </Link>
            ) : (
              <span className="text-xs text-muted-foreground">Restricted</span>
            )}
          </div>
          <div className="divide-y divide-border/70">
            {!canViewDeployments ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                <Circle className="mx-auto mb-2 h-7 w-7 opacity-25" />
                You do not have permission to view deployments.
              </div>
            ) : runningDeployments.length === 0 ? (
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
                      <p className="text-sm font-medium text-foreground">{job.model_name || "Unnamed model"}</p>
                      <p className="mt-1 text-xs text-muted-foreground">{formatState(state)}</p>
                    </div>
                    <Link
                      to={`/dashboard/deployments/${job.deployment_id}`}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40",
                        tone === "positive" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
                        tone === "warning" && "bg-amber-500/10 text-amber-700 dark:text-amber-400",
                        tone === "danger" && "bg-red-500/10 text-red-700 dark:text-red-400",
                        tone === "neutral" && "bg-muted text-muted-foreground",
                        tone === "locked" && "bg-muted text-muted-foreground"
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

        <div className="rounded-2xl border border-border/70 bg-card shadow-sm">
          <div className="flex items-center justify-between border-b border-border/70 px-4 py-3">
            <h3 className="text-sm font-semibold">Compute Pool Health</h3>
            {canViewDeployments ? (
              <Link
                to="/dashboard/compute/pools"
                className="text-xs font-semibold text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
              >
                Manage pools
              </Link>
            ) : (
              <span className="text-xs text-muted-foreground">Restricted</span>
            )}
          </div>
          <div className="divide-y divide-border/70">
            {!canViewDeployments ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                <Server className="mx-auto mb-2 h-7 w-7 opacity-25" />
                You do not have permission to view compute pools.
              </div>
            ) : poolList.length === 0 ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                <Server className="mx-auto mb-2 h-7 w-7 opacity-25" />
                No pools configured.
              </div>
            ) : (
              poolList.slice(0, 5).map((pool) => (
                <div key={pool.pool_id} className="flex items-center justify-between px-4 py-3">
                  <div>
                    <p className="text-sm font-medium text-foreground">{pool.pool_name}</p>
                    <p className="mt-1 text-xs capitalize text-muted-foreground">{pool.provider}</p>
                  </div>
                  <span
                    className={cn(
                      "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold",
                      isPoolHealthy(pool)
                        ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                        : ["terminated", "terminating"].includes((pool.lifecycle_state || "").toLowerCase())
                          ? "bg-red-500/10 text-red-700 dark:text-red-400"
                          : "bg-muted text-muted-foreground"
                    )}
                  >
                    {isPoolHealthy(pool) ? "Healthy" : ["terminated", "terminating"].includes((pool.lifecycle_state || "").toLowerCase()) ? "Terminated" : "Inactive"}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <section className="rounded-2xl border border-border/70 bg-card shadow-sm">
        <div className="flex items-center justify-between border-b border-border/70 px-4 py-3">
          <h3 className="text-sm font-semibold">Recent Inference Activity</h3>
          {canViewDeployments ? (
            <Link
              to="/dashboard/insights"
              className="text-xs font-semibold text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
            >
              Explore insights
            </Link>
          ) : (
            <span className="text-xs text-muted-foreground">Restricted</span>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th scope="col" className="px-4 py-3 font-medium">Timestamp</th>
                <th scope="col" className="px-4 py-3 font-medium">Deployment</th>
                <th scope="col" className="px-4 py-3 font-medium">Tokens</th>
                <th scope="col" className="px-4 py-3 font-medium">Latency</th>
                <th scope="col" className="px-4 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {!canViewDeployments ? (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-sm text-muted-foreground">
                    <div className="flex flex-col items-center gap-2">
                      <Activity className="h-7 w-7 opacity-30" />
                      You do not have permission to view inference activity.
                    </div>
                  </td>
                </tr>
              ) : recentLogs && recentLogs.length > 0 ? (
                recentLogs.map((log) => {
                  const isSuccess = !log.status_code || log.status_code < 400;
                  return (
                    <tr key={log.id} className="transition hover:bg-muted/30">
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
                            "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold",
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
