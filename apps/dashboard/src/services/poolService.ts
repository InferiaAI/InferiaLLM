/**
 * Pool-level API client.
 *
 * Wraps the orchestration-service endpoints that operate on compute_pools
 * rows rather than individual nodes.  The api_gateway proxies
 * /api/v1/deployment/* → orchestration deployment_server.py.
 */

import { computeApi } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PoolView {
  pool_id: string;
  pool_name: string;
  provider: string;
  is_active: boolean;
  owner_type: string;
  owner_id: string;
  allowed_gpu_types: string[];
  max_cost_per_hour: number;
  is_dedicated: boolean;
  scheduling_policy_json: string;
  provider_pool_id: string;
  provider_credential_name: string;
  cluster_id: string;
  pool_type: string;
  gpu_count: number;
  lifecycle_state: string;
  created_at: string;
  updated_at: string;
}

export interface PoolMetadataResponse {
  pool_id: string;
  provider: string;
  metadata: Record<string, unknown> | null;
  status: string;
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

/**
 * Fetch a single pool by ID.
 * Route: GET /api/v1/deployment/pool/{pool_id}
 */
export async function getPool(poolId: string): Promise<PoolView> {
  const res = await computeApi.get<PoolView>(`/deployment/pool/${poolId}`);
  return res.data;
}

/**
 * Fetch the current metadata for a pool without modifying it.
 *
 * The backend's PATCH /updatepool/{pool_id} skips the DB write when
 * ``metadata`` is null and still returns the current row, so we use it
 * as a lightweight read endpoint.
 *
 * Route: PATCH /api/v1/deployment/updatepool/{pool_id}
 */
export async function getPoolMetadata(
  poolId: string,
): Promise<Record<string, unknown>> {
  const res = await computeApi.patch<PoolMetadataResponse>(
    `/deployment/updatepool/${poolId}`,
    { metadata: null },
  );
  return (res.data.metadata as Record<string, unknown>) ?? {};
}

/**
 * Merge new metadata into the pool's metadata column.
 *
 * Route: PATCH /api/v1/deployment/updatepool/{pool_id}
 */
export async function patchPoolMetadata(
  poolId: string,
  metadata: Record<string, unknown>,
): Promise<PoolMetadataResponse> {
  const res = await computeApi.patch<PoolMetadataResponse>(
    `/deployment/updatepool/${poolId}`,
    { metadata },
  );
  return res.data;
}

/**
 * List all pools for an organisation.
 *
 * Route: GET /api/v1/deployment/listPools/{org_id}
 *
 * The backend wraps the array as `{"pools": [...]}`. Unwrap here so
 * callers receive a plain `PoolView[]`.
 */
export async function listPools(orgId: string): Promise<PoolView[]> {
  const res = await computeApi.get<{ pools: PoolView[] } | PoolView[]>(
    `/deployment/listPools/${orgId}`,
  );
  // Tolerate both shapes (production wraps as {pools:[…]}; tests/mocks may
  // return the array directly).
  if (Array.isArray(res.data)) return res.data;
  return res.data?.pools ?? [];
}

/**
 * Delete (destroy) a pool and every node in it.
 *
 * Route: DELETE /api/v1/deployment/pool/{pool_id}
 */
export async function deletePool(poolId: string): Promise<void> {
  await computeApi.delete(`/deployment/pool/${poolId}`);
}
