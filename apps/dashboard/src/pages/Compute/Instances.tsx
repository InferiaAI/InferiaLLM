import { useState, useEffect } from "react";
import {
  Play,
  RefreshCw,
  Search,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { computeApi } from "@/lib/api";

export default function Instances() {
  const navigate = useNavigate();
  const { user, organizations } = useAuth();
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState("");
  const [instances, setInstances] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const fetchPools = async () => {
    setIsLoading(true);
    try {
      // Fallback: Use org_id from user, or first organization in list
      const targetOrgId = user?.org_id || organizations?.[0]?.id;

      console.log("DEBUG Fetching pools for:", targetOrgId);

      if (!targetOrgId) {
        setIsLoading(false);
        return;
      }

      const res = await computeApi.get(`/deployment/listPools/${targetOrgId}`);
      setInstances(res.data.pools || []);
    } catch (error) {
      console.error("Error fetching pools:", error);
      toast.error("Failed to load compute pools");
    } finally {
      setIsLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    if (user || organizations.length > 0) fetchPools();
  }, [user, organizations]);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchPools();
  };

  const filteredInstances = instances.filter(
    (i) =>
      i.pool_name.toLowerCase().includes(search.toLowerCase()) ||
      i.pool_id.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="space-y-4 font-sans text-slate-900 dark:text-zinc-100">
      {/* Header */}
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-bold tracking-tight">Pools</h1>
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <button
              className="h-9 px-3 flex items-center gap-2 border rounded-md bg-white dark:bg-zinc-900 dark:border-zinc-800 hover:bg-slate-50 dark:hover:bg-zinc-800 transition-colors text-sm font-medium text-slate-700 dark:text-zinc-200 shadow-sm"
              onClick={handleRefresh}
            >
              <RefreshCw
                className={cn("w-3.5 h-3.5", refreshing && "animate-spin")}
              />{" "}
              Refresh
            </button>
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
              <input
                placeholder="Search pools..."
                className="h-9 w-64 rounded-md border dark:border-zinc-800 bg-white dark:bg-zinc-900 pl-9 pr-4 text-sm outline-none focus:ring-1 focus:ring-blue-500 shadow-sm placeholder:text-slate-400 dark:text-zinc-200"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          <div className="flex gap-2">
            <button
              onClick={() => navigate("/dashboard/compute/pools/new")}
              className="h-9 px-4 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 transition-colors shadow-sm flex items-center gap-2"
            >
              <Play className="w-4 h-4" />
              New
            </button>
          </div>
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
                <th className="px-4 py-3 font-medium">Pool Name</th>
                <th className="px-4 py-3 font-medium">Provider</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium text-right">ID</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-zinc-800">
              {isLoading ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-slate-500 dark:text-zinc-500">Loading pools...</td>
                </tr>
              ) : filteredInstances.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-slate-500 dark:text-zinc-500">No compute pools found. Create one to get started.</td>
                </tr>
              ) : (
                filteredInstances.map((instance) => (
                  <tr
                    key={instance.pool_id}
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
                        to={`/dashboard/compute/pools/${instance.pool_id}`}
                        className="hover:underline"
                      >
                        {instance.pool_name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-600 dark:text-zinc-400 text-xs capitalize">
                      {instance.provider}
                    </td>
                    <td className="px-4 py-3">
                      <span className={cn(
                        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded border text-xs font-medium",
                        instance.is_active
                          ? "border-green-200 bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800"
                          : "border-slate-200 bg-slate-50 text-slate-500 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700"
                      )}>
                        <div className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          instance.is_active ? "bg-green-500" : "bg-zinc-400"
                        )} />
                        {instance.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-slate-400 dark:text-zinc-500 font-mono text-xs">
                      {instance.pool_id.substring(0, 8)}...
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="bg-slate-50 dark:bg-zinc-900/50 border-t dark:border-zinc-800 px-4 py-3 flex items-center justify-between text-xs text-slate-500 dark:text-zinc-500 font-mono">
          <span>{filteredInstances.length} row(s) total.</span>
        </div>
      </div>
    </div>
  );
}
