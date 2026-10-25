import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { computeApi } from "@/lib/api";
import { deployRequests, type CatalogModel, type PendingDeploy } from "@/lib/workflow/readiness";
import { listPools } from "@/services/poolService";

// A deploy that ends in one of these never comes up, so the node stops waiting on it.
const DEAD_STATES = new Set(["FAILED", "TERMINATED", "STOPPED"]);

interface PreflightResponse {
  ready: boolean;
  checks: Array<{ passed: boolean; message?: string | null; check: string }>;
}

function errorMessage(err: unknown): string {
  const body = (err as { response?: { data?: { detail?: unknown; message?: unknown } } }).response?.data;
  const detail = body?.detail ?? body?.message;
  if (typeof detail === "string") return detail;
  return err instanceof Error ? err.message : "Request failed";
}

// Preflight, then deploy on the first usable pool, the same requests NewDeployment sends for a pool deploy.
export function useNodeDeploys() {
  const { user, organizations } = useAuth();
  const [pending, setPending] = useState<Record<string, PendingDeploy>>({});

  const set = useCallback((nodeId: string, p: PendingDeploy | null) => {
    setPending((prev) => {
      const next = { ...prev };
      if (p) next[nodeId] = p;
      else delete next[nodeId];
      return next;
    });
  }, []);

  const deploy = async (nodeId: string, entry: CatalogModel) => {
    const orgId = user?.org_id || organizations?.[0]?.id;
    if (!orgId) return set(nodeId, { status: "waiting", reason: "organization context missing" });
    set(nodeId, { status: "deploying", deploymentName: entry.deploymentName });
    try {
      const pool = (await listPools(orgId)).find((p) => p.is_active && p.lifecycle_state !== "terminated");
      if (!pool) return set(nodeId, { status: "waiting", reason: "no active compute pool" });
      const req = deployRequests(entry, pool.pool_id, { orgId, ownerId: user?.user_id });
      const { data } = await computeApi.post<PreflightResponse>("/deployment/preflight", req.preflight);
      if (!data.ready) {
        const failed = data.checks.filter((c) => !c.passed).map((c) => c.message || c.check);
        return set(nodeId, { status: "waiting", reason: failed.join("; ") || "preflight failed" });
      }
      const { data: created } = await computeApi.post<{ deployment_id?: string }>("/deployment/deploy", req.deploy);
      set(nodeId, { status: "deploying", deploymentName: entry.deploymentName, deploymentId: created?.deployment_id });
    } catch (err) {
      set(nodeId, { status: "waiting", reason: errorMessage(err) });
    }
  };

  // The ready-deployments list never shows a failed deploy, so poll each one we started.
  useEffect(() => {
    const watched = Object.entries(pending).flatMap(([nodeId, p]) =>
      p.status === "deploying" && p.deploymentId ? [[nodeId, p.deploymentId] as const] : [],
    );
    if (watched.length === 0) return;
    const timer = setInterval(() => {
      for (const [nodeId, id] of watched) {
        computeApi
          .get<{ state?: string; error_message?: string | null }>(`/deployment/status/${id}`)
          .then(({ data }) => {
            if (data.state && DEAD_STATES.has(data.state)) {
              set(nodeId, { status: "failed", reason: data.error_message || `deployment ${data.state.toLowerCase()}` });
            }
          })
          .catch(() => {});
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [pending, set]);

  const clear = useCallback((nodeId: string) => set(nodeId, null), [set]);
  const reset = useCallback(() => setPending({}), []);
  return { pending, deploy, clear, reset };
}
