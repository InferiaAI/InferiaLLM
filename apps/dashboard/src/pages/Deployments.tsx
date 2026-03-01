import { useMemo, useState } from "react";
import { computeApi } from "@/lib/api";
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
  AlertCircle,
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
  error_message?: string | null;
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
  errorMessage: string | null;
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
      return (res.data.deployments || []).map((d, index) => ({
        id: d.deployment_id || `temp-${index}`,
        name: d.model_name || `Deployment-${(d.deployment_id || "").slice(0, 8)}`,
        modelName: d.model_name || "-",
        provider: d.engine || "compute",
        endpointUrl: d.endpoint || "",
        orgId: d.org_id || "user",
        createdAt: d.created_at || new Date().toISOString(),
        status: (d.state || "PENDING").toUpperCase(),
        errorMessage: d.error_message || null,
      }));
    },
    enabled: !!targetOrgId,
    refetchOnMount: "always",
  });

  const stopMutation = useMutation({
    mutationFn: async (id: string) => { await computeApi.post("/deployment/terminate", { deployment_id: id }); },
    onSuccess: () => { toast.success("Deployment stopped"); queryClient.invalidateQueries({ queryKey: ["deployments"] }); },
    onError: (err: AxiosError<ApiErrorResponse>) => { toast.error(err.response?.data?.detail || "Failed to stop"); }
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => { await computeApi.delete(`/deployment/delete/${id}`); },
    onSuccess: () => { toast.success("Deployment deleted"); queryClient.invalidateQueries({ queryKey: ["deployments"] }); },
    onError: (err: AxiosError<ApiErrorResponse>) => { toast.error(err.response?.data?.detail || "Failed to delete"); }
  });

  const startMutation = useMutation({
    mutationFn: async (id: string) => { await computeApi.post("/deployment/start", { deployment_id: id }); },
    onSuccess: () => { toast.success("Deployment queued for start"); queryClient.invalidateQueries({ queryKey: ["deployments"] }); },
    onError: (err: AxiosError<ApiErrorResponse>) => { toast.error(err.response?.data?.detail || "Failed to start"); }
  });

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    return q ? deployments.filter(d => d.name.toLowerCase().includes(q) || d.modelName.toLowerCase().includes(q) || d.status.toLowerCase().includes(q)) : deployments;
  }, [deployments, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const paginated = useMemo(() => filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize), [filtered, currentPage, pageSize]);

  return (
    <div className="space-y-5 font-sans text-slate-900 dark:text-zinc-100">
      <DeploymentHeader onRefresh={() => queryClient.invalidateQueries({ queryKey: ["deployments"] })} onNew={() => navigate("/dashboard/deployments/new")} />

      <div className="flex items-center justify-between gap-3">
        <div className="relative w-full max-w-sm">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <input placeholder="Search deployments..." className="h-9 w-full rounded-md border dark:border-zinc-800 bg-white dark:bg-zinc-900 pl-9 pr-4 text-sm outline-none focus:ring-1 focus:ring-emerald-500 shadow-sm" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
        </div>
        <div className="text-xs text-muted-foreground">{filtered.length} deployments</div>
      </div>

      <div className="border rounded-xl bg-card overflow-hidden shadow-sm">
        <DeploymentTable
          deployments={paginated}
          isLoading={isLoading}
          isMutating={stopMutation.isPending || deleteMutation.isPending || startMutation.isPending}
          onStart={(id) => startMutation.mutate(id)}
          onStop={(id) => confirm("Stop this deployment?") && stopMutation.mutate(id)}
          onDelete={(id) => confirm("Permanently delete?") && deleteMutation.mutate(id)}
        />

        <DeploymentPagination
          totalItems={filtered.length}
          pageSize={pageSize}
          currentPage={currentPage}
          totalPages={totalPages}
          onPageChange={setPage}
          onPageSizeChange={(size) => { setPageSize(size); setPage(1); }}
        />
      </div>
    </div>
  );
}

function DeploymentHeader({ onRefresh, onNew }: { onRefresh: () => void; onNew: () => void }) {
  return (
    <div className="rounded-xl border bg-card p-5 shadow-sm">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Deployments</h1>
          <p className="mt-1 text-sm text-muted-foreground">Operate model runtimes, monitor state transitions, and access deployment settings.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="h-9 px-3 inline-flex items-center gap-2 border rounded-md bg-white dark:bg-zinc-900 dark:border-zinc-800 hover:bg-slate-50 transition-colors text-sm font-medium" onClick={onRefresh}><RefreshCw className="w-3.5 h-3.5" /> Refresh</button>
          <button type="button" onClick={onNew} className="h-9 px-4 bg-emerald-600 text-white rounded-md text-sm font-medium hover:bg-emerald-700 transition-colors shadow-sm inline-flex items-center gap-2"><Plus className="w-4 h-4" /> New Deployment</button>
        </div>
      </div>
    </div>
  );
}

function DeploymentTable({ deployments, isLoading, isMutating, onStart, onStop, onDelete }: { deployments: Deployment[]; isLoading: boolean; isMutating: boolean; onStart: (id: string) => void; onStop: (id: string) => void; onDelete: (id: string) => void }) {
  if (isLoading) {
    return (
      <div className="p-8">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-12 w-full bg-slate-100 dark:bg-zinc-800 animate-pulse rounded mb-2" />
        ))}
      </div>
    );
  }

  if (deployments.length === 0) {
    return (
      <div className="px-4 py-16 text-center text-slate-500">
        <Activity className="h-8 w-8 mx-auto mb-2 opacity-25" />
        <p className="text-sm">No deployments found.</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[980px] text-sm text-left">
        <thead className="bg-muted/50 text-muted-foreground border-b dark:bg-muted/20">
          <tr>
            <th className="px-6 py-3 font-medium">Deployment</th>
            <th className="px-6 py-3 font-medium">Model</th>
            <th className="px-6 py-3 font-medium">Provider</th>
            <th className="px-6 py-3 font-medium">Status</th>
            <th className="px-6 py-3 font-medium">Created On</th>
            <th className="px-6 py-3 font-medium text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {deployments.map((d) => (
            <DeploymentRow key={d.id} deployment={d} isMutating={isMutating} onStart={onStart} onStop={onStop} onDelete={onDelete} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DeploymentRow({ deployment: d, isMutating, onStart, onStop, onDelete }: { deployment: Deployment; isMutating: boolean; onStart: (id: string) => void; onStop: (id: string) => void; onDelete: (id: string) => void }) {
  const isRunning = ["READY", "RUNNING", "PENDING", "DEPLOYING"].includes(d.status);
  const canStart = ["STOPPED", "TERMINATED", "FAILED"].includes(d.status);
  const canDelete = ["STOPPED", "TERMINATED", "FAILED"].includes(d.status);

  return (
    <tr className="bg-background hover:bg-muted/50 dark:hover:bg-muted/10 transition-colors">
      <td className="px-6 py-4">
        <Link to={`/dashboard/deployments/${d.id}`} className="font-medium text-foreground hover:text-emerald-500 dark:hover:text-emerald-400 transition-colors">{d.name}</Link>
        <div className="mt-1 text-xs text-muted-foreground font-mono">{(d.id || "").slice(0, 12)}...</div>
      </td>
      <td className="px-6 py-4 text-muted-foreground font-mono text-xs">{d.modelName}</td>
      <td className="px-6 py-4 text-muted-foreground capitalize">{d.provider}</td>
      <td className="px-6 py-4">
        <span className={cn("inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-background border text-xs font-medium shadow-sm", getStatusStyles(d.status))}>
          <span className={cn("h-1.5 w-1.5 rounded-full", getStatusDot(d.status))} />
          {d.status}
        </span>
        {d.status === "FAILED" && d.errorMessage && (
          <div className="mt-1 flex items-start gap-1 text-[11px] text-red-600 dark:text-red-400 max-w-[280px]">
            <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" />
            <span className="line-clamp-2">{d.errorMessage}</span>
          </div>
        )}
      </td>
      <td className="px-6 py-4 text-muted-foreground text-xs">{new Date(d.createdAt).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</td>
      <td className="px-6 py-4">
        <div className="flex items-center justify-end gap-2">
          <Link to={`/dashboard/deployments/${d.id}`} className="inline-flex items-center gap-1.5 rounded-md border border-border/50 bg-background px-2.5 py-1.5 text-xs font-medium hover:bg-muted text-foreground transition-colors"><Settings className="w-3.5 h-3.5 text-muted-foreground" /> Settings</Link>
          {canStart && <button type="button" disabled={isMutating} onClick={() => onStart(d.id)} className="inline-flex items-center gap-1.5 rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2.5 py-1.5 text-xs text-emerald-600 dark:text-emerald-400 hover:bg-emerald-500/20 font-medium disabled:opacity-50 transition-colors"><Play className="w-3.5 h-3.5" /> Start</button>}
          {isRunning && <button type="button" disabled={isMutating} onClick={() => onStop(d.id)} className="inline-flex items-center gap-1.5 rounded-md border border-amber-500/20 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-600 dark:text-amber-400 hover:bg-amber-500/20 font-medium disabled:opacity-50 transition-colors"><Square className="w-3.5 h-3.5" /> Stop</button>}
          {canDelete && <button type="button" disabled={isMutating} onClick={() => onDelete(d.id)} className="inline-flex items-center gap-1.5 rounded-md border border-red-500/20 bg-red-500/10 px-2.5 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-500/20 font-medium disabled:opacity-50 transition-colors"><Trash2 className="w-3.5 h-3.5" /> Delete</button>}
        </div>
      </td>
    </tr>
  );
}

function DeploymentPagination({ totalItems, pageSize, currentPage, totalPages, onPageChange, onPageSizeChange }: { totalItems: number; pageSize: number; currentPage: number; totalPages: number; onPageChange: (p: number) => void; onPageSizeChange: (s: number) => void }) {
  return (
    <div className="bg-muted/10 border-t border-border/50 px-6 py-4 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between text-xs text-muted-foreground">
      <span>Showing {totalItems === 0 ? 0 : (currentPage - 1) * pageSize + 1} to {Math.min(currentPage * pageSize, totalItems)} of {totalItems}</span>
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2"><span>Rows</span><select className="bg-white dark:bg-zinc-900 border dark:border-zinc-800 rounded px-2 py-1 outline-none" value={pageSize} onChange={(e) => onPageSizeChange(Number(e.target.value))}>{PAGE_SIZE_OPTIONS.map((o) => (<option key={o} value={o}>{o}</option>))}</select></div>
        <div className="inline-flex items-center gap-2">
          <button type="button" className="rounded border px-2 py-1 disabled:opacity-50" disabled={currentPage <= 1} onClick={() => onPageChange(currentPage - 1)}>Prev</button>
          <span>Page {currentPage} of {totalPages}</span>
          <button type="button" className="rounded border px-2 py-1 disabled:opacity-50 inline-flex items-center gap-1" disabled={currentPage >= totalPages} onClick={() => onPageChange(currentPage + 1)}>Next <ArrowRight className="w-3 h-3" /></button>
        </div>
      </div>
    </div>
  );
}
