/**
 * /v1/nodes/* — node-centric admin client (replaces pool-based endpoints).
 *
 * The api_gateway proxies /api/v1/nodes/<path> straight through to the
 * orchestration service's /v1/nodes/<path> router.
 */

import { computeApi } from "@/lib/api";

export interface NodeView {
  id: string;
  pool_id: string | null;
  node_name: string | null;
  agent_kind: string | null;
  provider: string | null;
  state: string;
  labels: Record<string, string>;
  advertise_url: string | null;
  expose_url: string | null;
  gpu_total: number | null;
  gpu_allocated: number | null;
  vcpu_total: number | null;
  vcpu_allocated: number | null;
  ram_gb_total: number | null;
  ram_gb_allocated: number | null;
  last_heartbeat: string | null;
  provider_instance_id: string | null;
}

export interface AddWorkerNodeRequest {
  node_name: string;
  advertise_url?: string;
  labels?: Record<string, string>;
}

export interface AddWorkerNodeResponse {
  node_id: string;
  bootstrap_token: string;
  expires_at: number;
  control_plane_url: string;
  inference_token: string;
  env_snippet: string;
}

export interface AddProviderNodeRequest {
  node_name?: string;
  labels?: Record<string, string>;
  spec?: Record<string, unknown>;
  credential_name?: string;
}

export interface AddProviderNodeResponse {
  node_id: string;
  provider: string;
  provider_instance_id?: string | null;
  state: string;
}

export interface PatchLabelsRequest {
  add?: Record<string, string>;
  remove?: string[];
}

function serialiseSelector(selector?: Record<string, string>): string {
  if (!selector) return "";
  const parts = Object.entries(selector).map(([k, v]) => `${k}=${v}`);
  return parts.length ? `?labels=${encodeURIComponent(parts.join(","))}` : "";
}

export async function listNodes(
  selector?: Record<string, string>,
): Promise<NodeView[]> {
  const res = await computeApi.get<{ nodes: NodeView[] }>(
    `/nodes/${serialiseSelector(selector)}`,
  );
  return res.data?.nodes ?? [];
}

export async function getNode(nodeId: string): Promise<NodeView> {
  const res = await computeApi.get<NodeView>(`/nodes/${nodeId}`);
  return res.data;
}

export async function patchLabels(
  nodeId: string,
  body: PatchLabelsRequest,
): Promise<NodeView> {
  const res = await computeApi.patch<NodeView>(`/nodes/${nodeId}/labels`, body);
  return res.data;
}

export interface DeleteNodeResult {
  /** True when the backend kicked off an asynchronous EC2 destroy (202). */
  terminating: boolean;
  /** New row state when terminating=true. Always "terminating" today. */
  state?: string;
  /** node_id echoed by the backend on 202. */
  nodeId?: string;
}

export async function deleteNode(nodeId: string): Promise<DeleteNodeResult> {
  // The orchestration service returns 202 with a JSON body for AWS nodes
  // (background EC2 destroy is in flight) and 204 No Content for every
  // other provider. Both are success; we discriminate on the status to
  // tell the UI whether to start polling for terminal state.
  const res = await computeApi.delete(`/nodes/${nodeId}`);
  if (res.status === 202 && res.data) {
    return {
      terminating: true,
      state: res.data.state,
      nodeId: res.data.node_id,
    };
  }
  return { terminating: false };
}

export async function addWorkerNode(
  body: AddWorkerNodeRequest,
): Promise<AddWorkerNodeResponse> {
  const res = await computeApi.post<AddWorkerNodeResponse>(
    "/nodes/add/worker",
    body,
  );
  return res.data;
}

export async function addProviderNode(
  provider: "nosana" | "akash",
  body: AddProviderNodeRequest,
): Promise<AddProviderNodeResponse> {
  const res = await computeApi.post<AddProviderNodeResponse>(
    `/nodes/add/${provider}`,
    body,
  );
  return res.data;
}
