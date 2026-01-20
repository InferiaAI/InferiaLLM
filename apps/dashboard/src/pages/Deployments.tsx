import { useState } from "react";
import api from "@/lib/api";
import { toast } from "sonner";
import { Plus, RefreshCw, Settings, Search, Play, Square, Trash2 } from "lucide-react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { cn } from "@/lib/utils";

interface Deployment {
  id: string;
  name: string;
  model_name: string;
  provider: string; // "compute" or engine name (openai, anthropic, etc.)
  endpoint_url?: string;
  org_id: string;
  created_at: string;
  status: string;
}

import { useAuth } from "@/context/AuthContext";

export default function Deployments() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { user, organizations } = useAuth();
  const [search, setSearch] = useState("");

  // Data Fetching - Only from orchestration API (handles both compute and external deployments)
  const { data: deployments, isLoading } = useQuery<Deployment[]>({
    queryKey: ["deployments", user?.org_id],
    queryFn: async () => {
      const targetOrgId = user?.org_id || organizations?.[0]?.id;

      const res = await api.get("http://localhost:8080/deployment/deployments", {
        params: { org_id: targetOrgId }
      });

      return res.data.deployments.map((d: any) => ({
        id: d.deployment_id,
        name: d.model_name || `Deployment-${d.deployment_id.slice(0, 8)}`,
        model_name: d.model_name || '',
        provider: d.engine || "compute",
        endpoint_url: d.endpoint || "",
        org_id: d.org_id || "user",
        created_at: d.created_at || new Date().toISOString(),
        status: d.state || 'PENDING',
      }));
    },
    enabled: !!(user?.org_id || organizations?.[0]?.id),
  });

  // Stop/Terminate Mutation (for running deployments)
  const stopMutation = useMutation({
    mutationFn: async ({ id }: { id: string }) => {
      await api.post("http://localhost:8080/deployment/terminate", {
        deployment_id: id,
      });
    },
    onSuccess: () => {
      toast.success("Deployment stopped successfully");
      queryClient.invalidateQueries({ queryKey: ["deployments"] });
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to stop deployment");
    },
  });

  const handleStop = async (id: string) => {
    if (confirm("Are you sure you want to stop this deployment?")) {
      stopMutation.mutate({ id });
    }
  };

  // Delete Mutation (for permanently removing stopped deployments)
  const deleteMutation = useMutation({
    mutationFn: async ({ id, provider }: { id: string; provider: string }) => {
      if (provider === "compute" || provider === "vllm") {
        // Use the new hard delete endpoint
        await api.delete(`http://localhost:8080/deployment/delete/${id}`);
      } else {
        await api.delete(`/management/deployments/${id}`);
      }
    },
    onSuccess: () => {
      toast.success("Deployment deleted successfully");
      queryClient.invalidateQueries({ queryKey: ["deployments"] });
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to delete deployment");
    },
  });

  const handleDelete = async (id: string, provider: string) => {
    if (
      confirm(
        "Are you sure you want to permanently delete this deployment? This action cannot be undone.",
      )
    ) {
      deleteMutation.mutate({ id, provider });
    }
  };

  // Start Mutation
  const startMutation = useMutation({
    mutationFn: async ({ id }: { id: string }) => {
      await api.post("http://localhost:8080/deployment/start", {
        deployment_id: id,
      });
    },
    onSuccess: () => {
      toast.success("Deployment queued for start");
      queryClient.invalidateQueries({ queryKey: ["deployments"] });
    },
    onError: (err: any) => {
      toast.error(err.response?.data?.detail || "Failed to start deployment");
    }
  });

  const handleStart = async (id: string) => {
    startMutation.mutate({ id });
  };

  const filteredDeployments = deployments?.filter(
    (d) =>
      (d.name || '').toLowerCase().includes(search.toLowerCase()) ||
      (d.model_name || '').toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="space-y-4 font-sans text-slate-900 dark:text-zinc-100">
      {/* Header / Title */}
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-bold tracking-tight">Deployments</h1>
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <button
              className="h-9 px-3 flex items-center gap-2 border rounded-md bg-white dark:bg-zinc-900 dark:border-zinc-800 hover:bg-slate-50 dark:hover:bg-zinc-800 transition-colors text-sm font-medium text-slate-700 dark:text-zinc-200 shadow-sm"
              onClick={() =>
                queryClient.invalidateQueries({ queryKey: ["deployments"] })
              }
            >
              <RefreshCw className="w-3.5 h-3.5" /> Refresh
            </button>
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
              <input
                placeholder="Search deployments..."
                className="h-9 w-64 rounded-md border dark:border-zinc-800 bg-white dark:bg-zinc-900 pl-9 pr-4 text-sm outline-none focus:ring-1 focus:ring-blue-500 shadow-sm placeholder:text-slate-400 dark:text-zinc-200"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          <button
            onClick={() => navigate("/dashboard/deployments/new")}
            className="h-9 px-4 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 transition-colors shadow-sm flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            New
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-lg border dark:border-zinc-800 bg-white dark:bg-black shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-50 dark:bg-zinc-900 text-xs font-semibold text-slate-500 dark:text-zinc-400 uppercase tracking-wider border-b dark:border-zinc-800">
              <tr>
                <th className="px-4 py-3 w-8">
                  <input type="checkbox" className="rounded border-slate-300 dark:border-zinc-700 dark:bg-zinc-900" />
                </th>
                <th className="px-4 py-3 font-medium">Name</th>
                <th className="px-4 py-3 font-medium">Model</th>
                <th className="px-4 py-3 font-medium">Replicas</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium text-right">Created</th>
                <th className="px-4 py-3 w-10"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-zinc-800">
              {isLoading ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <tr key={i} className="animate-pulse">
                    <td className="px-4 py-3">
                      <div className="h-4 w-4 bg-slate-100 dark:bg-zinc-800 rounded"></div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-4 w-32 bg-slate-100 dark:bg-zinc-800 rounded"></div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-4 w-24 bg-slate-100 dark:bg-zinc-800 rounded"></div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-4 w-12 bg-slate-100 dark:bg-zinc-800 rounded"></div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-4 w-16 bg-slate-100 dark:bg-zinc-800 rounded"></div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-4 w-24 bg-slate-100 dark:bg-zinc-800 rounded ml-auto"></div>
                    </td>
                    <td className="px-4 py-3"></td>
                  </tr>
                ))
              ) : filteredDeployments?.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    className="px-4 py-12 text-center text-slate-500 dark:text-zinc-500"
                  >
                    No deployments found
                  </td>
                </tr>
              ) : (
                filteredDeployments?.map((d) => (
                  <tr
                    key={d.id}
                    className="group hover:bg-slate-50/80 dark:hover:bg-zinc-900/50 transition-colors"
                  >
                    <td className="px-4 py-3">
                      <input
                        type="checkbox"
                        className="rounded border-slate-300 dark:border-zinc-700 dark:bg-zinc-900"
                      />
                    </td>
                    <td className="px-4 py-3 font-medium text-blue-600 dark:text-blue-400">
                      <Link
                        to={`/dashboard/deployments/${d.id}`}
                        className="hover:underline"
                      >
                        {d.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-slate-600 dark:text-zinc-400 font-mono text-xs">
                      {d.model_name}
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-600 dark:text-zinc-400">
                      1 / 1
                    </td>
                    <td className="px-4 py-3">
                      <span className={cn(
                        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-xs font-medium",
                        d.status === "READY" || d.status === "RUNNING"
                          ? "border-green-200 bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800" :
                          d.status === "STOPPED"
                            ? "border-slate-200 bg-slate-50 text-slate-700 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700" :
                            d.status === "FAILED"
                              ? "border-red-200 bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800" :
                              "border-yellow-200 bg-yellow-50 text-yellow-700 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800"
                      )}>
                        <div className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          d.status === "READY" || d.status === "RUNNING" ? "bg-green-500" :
                            d.status === "STOPPED" ? "bg-zinc-400" :
                              d.status === "FAILED" ? "bg-red-500" : "bg-yellow-500"
                        )} />
                        {d.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-slate-500 dark:text-zinc-500 font-mono text-xs">
                      {
                        new Date(d.created_at)
                          .toISOString()
                          .split("T")[0]
                      }
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        {/* Start Button */}
                        {(d.status === "STOPPED" || d.status === "TERMINATED" || d.status === "FAILED") && (
                          <button
                            onClick={() => handleStart(d.id)}
                            className="p-1 text-slate-400 hover:text-green-600 dark:hover:text-green-400"
                            title="Start Deployment"
                          >
                            <Play className="w-4 h-4" />
                          </button>
                        )}

                        {/* Stop/End Button */}
                        {(d.status === "READY" || d.status === "RUNNING" || d.status === "PENDING" || d.status === "DEPLOYING") && (
                          <button
                            onClick={() => handleStop(d.id)}
                            className="p-1 text-slate-400 hover:text-amber-600 dark:hover:text-amber-400"
                            title="Stop Deployment"
                          >
                            <Square className="w-4 h-4" />
                          </button>
                        )}

                        {/* Delete Button (only for stopped/terminated/failed deployments) */}
                        {(d.status === "STOPPED" || d.status === "TERMINATED" || d.status === "FAILED") && (
                          <button
                            onClick={() => handleDelete(d.id, d.provider)}
                            className="p-1 text-slate-400 hover:text-red-600 dark:hover:text-red-400"
                            title="Delete Deployment"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        )}

                        <Link
                          to={`/dashboard/deployments/${d.id}`}
                          className="p-1 text-slate-400 hover:text-blue-600 dark:hover:text-blue-400"
                          title="Settings"
                        >
                          <Settings className="w-4 h-4" />
                        </Link>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="bg-slate-50 dark:bg-zinc-900/50 border-t dark:border-zinc-800 px-4 py-3 flex items-center justify-between text-xs text-slate-500 dark:text-zinc-500 font-mono">
          <span>{filteredDeployments?.length || 0} row(s) total.</span>
          <div className="flex items-center gap-2">
            <span>Rows per page:</span>
            <select className="bg-white dark:bg-zinc-900 border dark:border-zinc-800 rounded px-2 py-0.5 outline-none">
              <option>20</option>
              <option>50</option>
              <option>100</option>
            </select>
            <span className="ml-4">Page 1 of 1</span>
          </div>
        </div>
      </div>
    </div>
  );
}
