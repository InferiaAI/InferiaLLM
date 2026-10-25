import type { Deployment } from "@/hooks/useReadyDeployments";
import { NODE_KINDS, matchingDeployments } from "./nodeKinds";
import type { NodeKind, Workflow, WorkflowNode } from "./types";
import { buildDiffusionSpec } from "@/pages/newDeploymentSpec";

export interface CatalogModel {
  modelId: string;
  engine: string;
  modelType: "inference" | "audio_generation" | "image_generation";
  // Becomes the deployment's model_name, which is also the name the inference API routes on.
  deploymentName: string;
  job: Record<string, unknown>;
}

const ollama = (modelId: string) => ({
  model_id: modelId,
  engine: "ollama",
  image: "ollama/ollama:latest",
  cmd: ["serve"],
  expose: [{ port: 11434, type: "http" }],
  gpu: true,
});

// Only engines that worker pools have a recipe for. Speech has none yet, so
// speech nodes ask for an external deployment instead of offering Deploy.
export const MODEL_CATALOG: Partial<Record<NodeKind, CatalogModel>> = {
  llm: {
    modelId: "qwen2.5:1.5b",
    engine: "ollama",
    modelType: "inference",
    deploymentName: "qwen2-5-1-5b",
    job: ollama("qwen2.5:1.5b"),
  },
  vision: {
    modelId: "moondream",
    engine: "ollama",
    modelType: "inference",
    deploymentName: "moondream-vision",
    job: ollama("moondream"),
  },
  imageGeneration: {
    modelId: "stabilityai/sd-turbo",
    engine: "inferia-diffusion",
    modelType: "image_generation",
    deploymentName: "sd-turbo-image",
    job: JSON.parse(buildDiffusionSpec({ modelId: "stabilityai/sd-turbo", modelType: "image_generation" })),
  },
};

// Deploy attempt the canvas started for a node; lives in UI state, never in the saved workflow.
export type PendingDeploy =
  | { status: "deploying"; deploymentName: string; deploymentId?: string }
  | { status: "waiting"; reason: string }
  | { status: "failed"; reason: string };

export type Readiness =
  | { state: "ready" }
  | { state: "selectModel" }
  | { state: "needsExternal" }
  | { state: "needsDeployment"; recommended: CatalogModel }
  | { state: "deploying"; recommended: CatalogModel }
  | { state: "waiting"; recommended: CatalogModel; reason: string }
  | { state: "failed"; recommended: CatalogModel; reason: string }
  // The node's own deployment came up; the canvas selects it and the node becomes ready.
  | { state: "deployed"; model: string };

export function nodeReadiness(
  node: Pick<WorkflowNode, "kind" | "config">,
  deployments: Deployment[],
  pending?: PendingDeploy,
): Readiness {
  const field = NODE_KINDS[node.kind].fields.find((f) => f.type === "model");
  if (!field || field.type !== "model" || node.config[field.key]) return { state: "ready" };
  const recommended = MODEL_CATALOG[node.kind];
  if (pending?.status === "deploying" && deployments.some((d) => d.modelName === pending.deploymentName)) {
    return { state: "deployed", model: pending.deploymentName };
  }
  if (matchingDeployments(field, deployments).length > 0) return { state: "selectModel" };
  if (!recommended) return { state: "needsExternal" };
  if (pending?.status === "deploying") return { state: "deploying", recommended };
  if (pending?.status === "waiting") return { state: "waiting", recommended, reason: pending.reason };
  if (pending?.status === "failed") return { state: "failed", recommended, reason: pending.reason };
  return { state: "needsDeployment", recommended };
}

export function readinessMessage(r: Readiness): string | null {
  switch (r.state) {
    case "ready":
    case "deployed":
      return null;
    case "selectModel":
      return "Select a model";
    case "needsExternal":
      return "Needs an external deployment";
    case "needsDeployment":
      return "Needs deployment";
    case "deploying":
      return `Deploying ${r.recommended.deploymentName}`;
    case "waiting":
      return `Waiting for compute: ${r.reason}`;
    case "failed":
      return `Deploy failed: ${r.reason}`;
  }
}

export function runBlocker(
  workflow: Workflow,
  deployments: Deployment[],
  pending: Record<string, PendingDeploy>,
): string | null {
  for (const node of workflow.nodes) {
    const message = readinessMessage(nodeReadiness(node, deployments, pending[node.id]));
    if (message) return `${NODE_KINDS[node.kind].label}: ${message}`;
  }
  return null;
}

export function deployRequests(entry: CatalogModel, poolId: string, ids: { orgId: string; ownerId?: string }) {
  return {
    preflight: { model_id: entry.modelId, engine: entry.engine, gpu_per_replica: 1, pool_id: poolId, model_type: entry.modelType },
    deploy: {
      model_name: entry.deploymentName,
      model_version: "latest",
      replicas: 1,
      gpu_per_replica: entry.job.gpu ? 1 : 0,
      workload_type: "inference",
      pool_id: poolId,
      engine: entry.engine,
      model_type: entry.modelType,
      configuration: entry.job,
      job_definition: entry.job,
      inference_model: entry.modelId,
      owner_id: ids.ownerId,
      org_id: ids.orgId,
    },
  };
}
