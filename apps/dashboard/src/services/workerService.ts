/**
 * inferia-worker admin client.
 *
 * Hits the api_gateway-proxied `/admin/workers/...` endpoints which forward
 * to the orchestration service's `/v1/admin/workers/...` router.
 */

import { computeApi } from "@/lib/api";

export interface MintBootstrapTokenRequest {
  pool_id: string;
  ttl_hours?: number;
}

export interface MintBootstrapTokenResponse {
  bootstrap_token: string;
  expires_at: number;
  pool_id: string;
  control_plane_url: string;
  inference_token: string;
  env_snippet: string;
}

export interface WorkerView {
  node_id: string;
  node_name: string | null;
  advertise_url: string | null;
  agent_kind: string;
  state: string;
  connected: boolean;
  last_heartbeat: string | null;
  used: Record<string, string>;
  loaded_models: string[];
  allocatable: Record<string, string>;
}

export interface ListWorkersResponse {
  workers: WorkerView[];
}

export async function mintBootstrapToken(
  body: MintBootstrapTokenRequest,
): Promise<MintBootstrapTokenResponse> {
  const res = await computeApi.post<MintBootstrapTokenResponse>(
    "/admin/workers/tokens",
    body,
  );
  return res.data;
}

export async function listWorkers(poolId: string): Promise<WorkerView[]> {
  const res = await computeApi.get<ListWorkersResponse>(
    `/admin/workers/pool/${poolId}`,
  );
  return res.data?.workers ?? [];
}

export async function revokeWorker(nodeId: string): Promise<void> {
  await computeApi.delete(`/admin/workers/${nodeId}`);
}

export interface GPUSample {
  index: number;
  name: string;
  util_pct: number;
  mem_used_mib: number;
  mem_total_mib: number;
}

export interface NodeMetricsSample {
  ts: string;
  cpu_pct: number;
  mem_used_bytes: number;
  mem_total_bytes: number;
  net_rx_bps: number;
  net_tx_bps: number;
  disk_read_bps: number;
  disk_write_bps: number;
  gpus: GPUSample[];
}

export interface NodeMetricsResponse {
  latest: NodeMetricsSample | null;
  samples: NodeMetricsSample[];
}

export async function getNodeMetrics(nodeId: string): Promise<NodeMetricsResponse> {
  const res = await computeApi.get<NodeMetricsResponse>(
    `/admin/workers/${nodeId}/metrics`,
  );
  return res.data ?? { latest: null, samples: [] };
}
