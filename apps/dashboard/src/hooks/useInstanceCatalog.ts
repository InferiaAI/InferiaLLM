/**
 * useInstanceCatalog — TanStack Query hook that fetches the AWS EC2 instance
 * catalog, now region-aware with curated fallback.
 *
 * When a `region` is provided the hook first attempts a live AWS discovery
 * call via ConfigService.listAwsInstanceTypes(region). If the live call
 * returns real data (fallback === false and a non-empty list) the live
 * catalog is used, grouped by class. Otherwise — or when no region is
 * supplied — it falls back to the static curated catalog served by
 * /providers/aws/instance-catalog.
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
 * The catalog is cached for 5 minutes per (region | "curated") key —
 * switching regions or navigating away and back never re-fetches in
 * that window unless the key changes.
 */
import { useQuery } from "@tanstack/react-query";
import { computeApi } from "@/lib/api";
import { ConfigService, type AwsInstanceType } from "@/services/configService";

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

function groupLiveTypes(types: AwsInstanceType[]): InstanceCatalog {
  const out: InstanceCatalog = { normal_gpu: [], heavy_gpu: [], cpu: [] };
  for (const t of types) {
    const cls: InstanceClass =
      t.gpu_count > 1 ? "heavy_gpu" : t.gpu_count === 1 ? "normal_gpu" : "cpu";
    out[cls].push({
      name: t.instance_type,
      cls,
      vcpu: t.vcpus,
      ram_gb: t.memory_gb,
      gpu_count: t.gpu_count,
      gpu_model: t.gpu_model,
      gpu_ram_gb: 0,       // live discovery doesn't expose per-GPU VRAM
      price_per_hour: 0,   // live discovery has no pricing
    });
  }
  return out;
}

export function useInstanceCatalog(region?: string) {
  return useQuery<InstanceCatalog>({
    queryKey: ["aws-instance-catalog", region ?? "curated"],
    queryFn: async () => {
      if (region) {
        try {
          const live = await ConfigService.listAwsInstanceTypes(region);
          if (!live.fallback && live.instance_types.length) {
            return groupLiveTypes(live.instance_types);
          }
        } catch {
          // live discovery failed (network / 5xx / auth) — fall through to curated
        }
      }
      // No region given, or live returned fallback/empty — use curated catalog.
      // Use the authenticated axios client so the JWT goes through. Raw
      // fetch() skips the interceptor → gateway returns 401 → empty
      // catalog → no GPU types in NewPool's instance selector.
      const resp = await computeApi.get<InstanceCatalog>(
        "/providers/aws/instance-catalog",
      );
      return resp.data;
    },
    // 5 minutes — the catalog is a static module on the backend.
    staleTime: 5 * 60 * 1000,
  });
}
