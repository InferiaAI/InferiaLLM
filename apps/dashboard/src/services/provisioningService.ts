/**
 * /v1/nodes/{id}/provisioning + /provisioning-logs + /ec2-console clients.
 *
 * The api_gateway proxies these paths through to the orchestration service,
 * same as the other /nodes/* endpoints in nodeService.ts.
 */
import { computeApi } from "@/lib/api";

export type PhaseStatus = "pending" | "running" | "succeeded" | "failed";

export interface ProvisioningPhase {
  phase: string;
  status: PhaseStatus;
  started_at: string | null;
  ended_at: string | null;
  last_message: string | null;
}

export interface ProvisioningSummary {
  current_phase: string | null;
  terminal: boolean;
  phases: ProvisioningPhase[];
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

export const ALL_PHASES = [
  "prepare", "ami_lookup", "pulumi_init", "pulumi_up",
  "ec2_running", "cloud_init", "worker_bootstrap", "ready",
] as const;
