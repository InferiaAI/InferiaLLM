/**
 * /v1/nodes/{id}/provisioning + /provisioning-logs + /ec2-console clients.
 *
 * The api_gateway proxies these paths through to the orchestration service,
 * same as the other /nodes/* endpoints in nodeService.ts.
 */
import { computeApi } from "@/lib/api";
import type { AWSMetadata } from "@/components/nodes/AWSMetadataGrid";

export type PhaseStatus = "pending" | "running" | "succeeded" | "failed";

export interface ProvisioningPhase {
  phase: string;
  status: PhaseStatus;
  started_at: string | null;
  ended_at: string | null;
  last_message: string | null;
}

// Mirrors the orchestration ProvisioningSummary.error_block contract:
// {code, message, hint, class}. `class` is a reserved word so the consuming
// code spells it `errorClass` when reading the field; we keep the wire name.
export interface ProvisioningError {
  code: string;
  message: string | null;
  hint: string | null;
  // Wire name retains "class" because the backend dict uses that key.
  class: string;
}

export interface ProvisioningSummary {
  current_phase: string | null;
  terminal: boolean;
  phases: ProvisioningPhase[];
  // T24 / T30 additions. attempt_count drives the "attempt N" badge,
  // error drives the red banner + Retry button, aws_metadata drives the
  // AWSMetadataGrid, job_id surfaces the backend job row for the retry
  // POST handler.
  attempt_count?: number;
  error?: ProvisioningError | null;
  aws_metadata?: AWSMetadata | null;
  job_id?: string | null;
}

export interface ProvisioningEvent {
  id: number;
  phase: string;
  status: PhaseStatus | "log";
  message: string | null;
  created_at: string;
}

export interface ProvisioningLogsResponse {
  events: ProvisioningEvent[];
  next_after: number | null;
}

export interface EC2ConsoleResponse {
  logs: string[];
  fetched_at: string;
}

export async function getProvisioning(nodeId: string): Promise<ProvisioningSummary> {
  const r = await computeApi.get(`/nodes/${nodeId}/provisioning`);
  return r.data;
}

export async function getProvisioningLogs(
  nodeId: string,
  after: number = 0,
): Promise<ProvisioningLogsResponse> {
  const r = await computeApi.get(`/nodes/${nodeId}/provisioning-logs?after=${after}`);
  return r.data;
}

export async function getEC2Console(nodeId: string): Promise<EC2ConsoleResponse> {
  const r = await computeApi.get(`/nodes/${nodeId}/ec2-console`);
  return r.data;
}

// Coarse provisioning phases emitted by the reconciler state machine
// (orchestration jobs/model.py::Phase). The legacy fine-grained phase set
// is gone; the timeline must mirror exactly what the backend emits into
// node_provisioning_events or every row renders "pending".
export const ALL_PHASES = [
  "preflight", "provisioning", "bootstrapping", "ready",
] as const;

export async function retryProvisioning(nodeId: string): Promise<{ job_id: string; phase: string }> {
  const res = await computeApi.post(`/nodes/${nodeId}/provisioning/retry`, {});
  return res.data;
}
