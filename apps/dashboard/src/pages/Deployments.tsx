import { useMemo, useState } from "react";
import { managementApi, computeApi } from "@/lib/api";
import { toast } from "sonner";
import {
  Plus,
  RefreshCw,
  Settings,
  Search,
  Play,
  Square,
  Trash2,
  ArrowRight,
  Activity,
} from "lucide-react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";
import { useAuth } from "@/context/AuthContext";
import type { AxiosError } from "axios";

interface DeploymentRecord {
  deployment_id: string;
  model_name?: string;
  engine?: string;
  endpoint?: string;
  org_id?: string;
  created_at?: string;
  state?: string;
}

interface DeploymentResponse {
  deployments: DeploymentRecord[];
}

interface Deployment {
  id: string;
  name: string;
  modelName: string;
  provider: string;
  endpointUrl: string;
  orgId: string;
  createdAt: string;
  status: string;
}

type ApiErrorResponse = {
  detail?: string;
};

const PAGE_SIZE_OPTIONS = [20, 50, 100];

function getStatusStyles(status: string) {
  if (status === "READY" || status === "RUNNING") {
    return "border-green-200 bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800";
  }
  if (status === "STOPPED" || status === "TERMINATED") {
    return "border-slate-200 bg-slate-50 text-slate-700 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700";
  }
  if (status === "FAILED") {
    return "border-red-200 bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800";
  }
  return "border-yellow-200 bg-yellow-50 text-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800";
}

function getStatusDot(status: string) {
  if (status === "READY" || status === "RUNNING") return "bg-green-500";
  if (status === "STOPPED" || status === "TERMINATED") return "bg-zinc-400";
  if (status === "FAILED") return "bg-red-500";
  return "bg-yellow-500";
}

export default function Deployments() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { user, organizations } = useAuth();

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const targetOrgId = user?.org_id || organizations?.[0]?.id;

  const { data: deployments = [], isLoading } = useQuery<Deployment[]>({
    queryKey: ["deployments", targetOrgId],
    queryFn: async () => {
      const res = await computeApi.get<DeploymentResponse>("/deployment/deployments", {
        params: { org_id: targetOrgId },
      });

      return (res.data.deployments || []).map((d) => ({
        id: d.deployment_id,
        name: d.model_name || `Deployment-${d.deployment_id.slice(0, 8)}`,
        modelName: d.model_name || "-",
        provider: d.engine || "compute",
        endpointUrl: d.endpoint || "",
        orgId: d.org_id || "user",
        createdAt: d.created_at || new Date().toISOString(),
        status: (d.state || "PENDING").toUpperCase(),
      }));
    },
    enabled: !!targetOrgId,
  });

  const stopMutation = useMutation({
    mutationFn: async ({ id }: { id: string }) => {
      await computeApi.post("/deployment/terminate", { deployment_id: id });
    },
    onSuccess: () => {
      toast.success("Deployment stopped successfully");
      queryClient.invalidateQueries({ queryKey: ["deployments"] });
    },
    onError: (error: AxiosError<ApiErrorResponse>) => {
      toast.error(error.response?.data?.detail || "Failed to stop deployment");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async ({ id, provider }: { id: string; provider: string }) => {
      if (provider === "compute" || provider === "vllm") {
        await computeApi.delete(`/deployment/delete/${id}`);
      } else {
        await managementApi.delete(`/management/deployments/${id}`);
      }
    },
    onSuccess: () => {
      toast.success("Deployment deleted successfully");
      queryClient.invalidateQueries({ queryKey: ["deployments"] });
    },
    onError: (error: AxiosError<ApiErrorResponse>) => {
      toast.error(error.response?.data?.detail || "Failed to delete deployment");
    },
  });

  const startMutation = useMutation({
    mutationFn: async ({ id }: { id: string }) => {
      await computeApi.post("/deployment/start", { deployment_id: id });
    },
    onSuccess: () => {
      toast.success("Deployment queued for start");
      queryClient.invalidateQueries({ queryKey: ["deployments"] });
    },
    onError: (error: AxiosError<ApiErrorResponse>) => {
      toast.error(error.response?.data?.detail || "Failed to start deployment");
    },
  });

  const filteredDeployments = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return deployments;
    return deployments.filter(
      (deployment) =>
        deployment.name.toLowerCase().includes(q) ||
        deployment.modelName.toLowerCase().includes(q) ||
        deployment.status.toLowerCase().includes(q)
    );
  }, [deployments, search]);

  const totalPages = Math.max(1, Math.ceil(filteredDeployments.length / pageSize));
  const currentPage = Math.min(page, totalPages);

  const paginatedDeployments = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredDeployments.slice(start, start + pageSize);
  }, [filteredDeployments, currentPage, pageSize]);

  const canGoPrev = currentPage > 1;
  const canGoNext = currentPage < totalPages;

  const handleStop = (id: string) => {
    if (confirm("Are you sure you want to stop this deployment?")) {
      stopMutation.mutate({ id });
    }
  };

  const handleDelete = (id: string, provider: string) => {
    if (confirm("Are you sure you want to permanently delete this deployment? This action cannot be undone.")) {
      deleteMutation.mutate({ id, provider });
    }
  };

  const isMutating = stopMutation.isPending || deleteMutation.isPending || startMutation.isPending;

  return (
    <div className="space-y-5 font-sans text-slate-900 dark:text-zinc-100">
      <div className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Deployments</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Operate model runtimes, monitor state transitions, and access deployment settings.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="h-9 px-3 inline-flex items-center gap-2 border rounded-md bg-white dark:bg-zinc-900 dark:border-zinc-800 hover:bg-slate-50 dark:hover:bg-zinc-800 transition-colors text-sm font-medium"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["deployments"] })}
            >
              <RefreshCw className="w-3.5 h-3.5" /> Refresh
            </button>
            <button
              type="button"
              onClick={() => navigate("/dashboard/deployments/new")}
              className="h-9 px-4 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 transition-colors shadow-sm inline-flex items-center gap-2"
            >
              <Plus className="w-4 h-4" /> New Deployment
            </button>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between gap-3">
        <div className="relative w-full max-w-sm">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <input
            placeholder="Search by deployment, model, or status"
            className="h-9 w-full rounded-md border dark:border-zinc-800 bg-white dark:bg-zinc-900 pl-9 pr-4 text-sm outline-none focus:ring-1 focus:ring-blue-500 shadow-sm placeholder:text-slate-400 dark:text-zinc-200"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
        </div>
        <div className="text-xs text-muted-foreground">
          {filteredDeployments.length} deployment{filteredDeployments.length === 1 ? "" : "s"}
        </div>
      </div>

      <div className="rounded-xl border dark:border-zinc-800 bg-white dark:bg-black shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-sm text-left">
            <thead className="bg-slate-50 dark:bg-zinc-900 text-xs font-semibold text-slate-500 dark:text-zinc-400 uppercase tracking-wider border-b dark:border-zinc-800">
              <tr>
                <th className="px-4 py-3 font-medium">Deployment</th>
                <th className="px-4 py-3 font-medium">Model</th>
                <th className="px-4 py-3 font-medium">Provider</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Created</th>
                <th className="px-4 py-3 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-zinc-800">
              {isLoading ? (
                Array.from({ length: 6 }).map((_, index) => (
                  <tr key={index} className="animate-pulse">
                    <td className="px-4 py-3"><div className="h-4 w-48 bg-slate-100 dark:bg-zinc-800 rounded" /></td>
                    <td className="px-4 py-3"><div className="h-4 w-36 bg-slate-100 dark:bg-zinc-800 rounded" /></td>
                    <td className="px-4 py-3"><div className="h-4 w-20 bg-slate-100 dark:bg-zinc-800 rounded" /></td>
                    <td className="px-4 py-3"><div className="h-4 w-20 bg-slate-100 dark:bg-zinc-800 rounded" /></td>
                    <td className="px-4 py-3"><div className="h-4 w-24 bg-slate-100 dark:bg-zinc-800 rounded" /></td>
                    <td className="px-4 py-3"><div className="h-4 w-36 ml-auto bg-slate-100 dark:bg-zinc-800 rounded" /></td>
                  </tr>
                ))
              ) : paginatedDeployments.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-16 text-center text-slate-500 dark:text-zinc-500">
                    <Activity className="h-8 w-8 mx-auto mb-2 opacity-25" />
                    <p className="text-sm">No deployments match your current filter.</p>
                  </td>
                </tr>
              ) : (
                paginatedDeployments.map((deployment) => {
                  const isRunning = ["READY", "RUNNING", "PENDING", "DEPLOYING"].includes(deployment.status);
                  const canStart = ["STOPPED", "TERMINATED", "FAILED"].includes(deployment.status);
                  const canDelete = ["STOPPED", "TERMINATED", "FAILED"].includes(deployment.status);

                  return (
                    <tr key={deployment.id} className="hover:bg-slate-50/80 dark:hover:bg-zinc-900/50 transition-colors">
                      <td className="px-4 py-3">
                        <Link to={`/dashboard/deployments/${deployment.id}`} className="font-medium text-blue-600 dark:text-blue-400 hover:underline">
                          {deployment.name}
                        </Link>
                        <div className="mt-1 text-xs text-muted-foreground font-mono">{deployment.id.slice(0, 12)}...</div>
                      </td>
                      <td className="px-4 py-3 text-slate-600 dark:text-zinc-300 font-mono text-xs">{deployment.modelName}</td>
                      <td className="px-4 py-3 text-slate-600 dark:text-zinc-300 capitalize">{deployment.provider}</td>
                      <td className="px-4 py-3">
                        <span className={cn("inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-xs font-medium", getStatusStyles(deployment.status))}>
                          <span className={cn("h-1.5 w-1.5 rounded-full", getStatusDot(deployment.status))} />
                          {deployment.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-500 dark:text-zinc-500 text-xs">
                        {new Date(deployment.createdAt).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-2">
                          <Link
                            to={`/dashboard/deployments/${deployment.id}`}
                            className="inline-flex items-center gap-1 rounded-md border px-2.5 py-1.5 text-xs hover:bg-muted"
                            title="Open settings"
                          >
                            <Settings className="w-3.5 h-3.5" />
                            Settings
                          </Link>

                          {canStart && (
                            <button
                              type="button"
                              disabled={isMutating}
                              onClick={() => startMutation.mutate({ id: deployment.id })}
                              className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-xs text-emerald-700 hover:bg-emerald-100 dark:border-emerald-800 dark:bg-emerald-900/25 dark:text-emerald-300 dark:hover:bg-emerald-900/40 disabled:opacity-60"
                            >
                              <Play className="w-3.5 h-3.5" /> Start
                            </button>
                          )}

                          {isRunning && (
                            <button
                              type="button"
                              disabled={isMutating}
                              onClick={() => handleStop(deployment.id)}
                              className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-700 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-900/25 dark:text-amber-300 dark:hover:bg-amber-900/40 disabled:opacity-60"
                            >
                              <Square className="w-3.5 h-3.5" /> Stop
                            </button>
                          )}

                          {canDelete && (
                            <button
                              type="button"
                              disabled={isMutating}
                              onClick={() => handleDelete(deployment.id, deployment.provider)}
                              className="inline-flex items-center gap-1 rounded-md border border-red-200 bg-red-50 px-2.5 py-1.5 text-xs text-red-700 hover:bg-red-100 dark:border-red-800 dark:bg-red-900/25 dark:text-red-300 dark:hover:bg-red-900/40 disabled:opacity-60"
                            >
                              <Trash2 className="w-3.5 h-3.5" /> Delete
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="bg-slate-50 dark:bg-zinc-900/50 border-t dark:border-zinc-800 px-4 py-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between text-xs text-slate-500 dark:text-zinc-500">
          <span>
            Showing {paginatedDeployments.length === 0 ? 0 : (currentPage - 1) * pageSize + 1} to {Math.min(currentPage * pageSize, filteredDeployments.length)} of {filteredDeployments.length}
          </span>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span>Rows</span>
              <select
                className="bg-white dark:bg-zinc-900 border dark:border-zinc-800 rounded px-2 py-1 outline-none"
                value={pageSize}
                onChange={(event) => {
                  setPageSize(Number(event.target.value));
                  setPage(1);
                }}
              >
                {PAGE_SIZE_OPTIONS.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
            </div>
            <div className="inline-flex items-center gap-2">
              <button
                type="button"
                className="rounded border px-2 py-1 disabled:opacity-50"
                disabled={!canGoPrev}
                onClick={() => setPage((value) => Math.max(value - 1, 1))}
              >
                Prev
              </button>
              <span>Page {currentPage} of {totalPages}</span>
              <button
                type="button"
                className="rounded border px-2 py-1 disabled:opacity-50 inline-flex items-center gap-1"
                disabled={!canGoNext}
                onClick={() => setPage((value) => Math.min(value + 1, totalPages))}
              >
                Next <ArrowRight className="w-3 h-3" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
