/**
 * useInstanceCatalog — TanStack Query hook that fetches the curated
 * AWS EC2 catalog from the orchestration service.
 *
 * The endpoint groups instances by class:
 *   - normal_gpu  → single-GPU instances for routine inference
 *   - heavy_gpu   → multi-GPU and high-end (A100/H100) instances
 *   - cpu         → no-GPU instances for cheap/control-plane workloads
 *
 * Field-name note: the backend dataclass stores the column as
 * `approx_usd_per_hour`, but T22 renames it to `price_per_hour` at the
 * HTTP boundary to match the existing dashboard contract (see
 * package/src/inferia/services/orchestration/api/providers.py and
 * apps/dashboard/src/pages/Compute/NewPool.tsx). We use that wire name
 * here so consumers do not have to remember the rename.
 *
 * The catalog rarely changes (it lives in a static Python module on
 * the backend), so we cache it for 5 minutes — switching tier tabs or
 * navigating away and back never re-fetches in that window.
 */
import { useQuery } from "@tanstack/react-query";

export type InstanceClass = "normal_gpu" | "heavy_gpu" | "cpu";

export type InstanceType = {
  name: string;
  cls: InstanceClass;
  vcpu: number;
  ram_gb: number;
  gpu_count: number;
  gpu_model: string | null;
  gpu_ram_gb: number;
  // Backend dataclass field is `approx_usd_per_hour`; the HTTP endpoint
  // renames it at serialization. See providers.py::_to_dict.
  price_per_hour: number;
};

export type InstanceCatalog = Record<InstanceClass, InstanceType[]>;

export function useInstanceCatalog() {
  return useQuery<InstanceCatalog>({
    queryKey: ["aws-instance-catalog"],
    queryFn: async () => {
      const resp = await fetch("/api/v1/providers/aws/instance-catalog");
      if (!resp.ok) {
        throw new Error(`catalog fetch failed: ${resp.status}`);
      }
      return resp.json();
    },
    // 5 minutes — the catalog is a static module on the backend.
    staleTime: 5 * 60 * 1000,
  });
}
