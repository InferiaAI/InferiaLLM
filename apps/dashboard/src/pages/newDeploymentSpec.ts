// Pure job-spec builders for the New Deployment form. Kept out of the large
// NewDeployment.tsx component so they can be unit-tested and coverage-measured
// in isolation (see vitest.config.ts coverage.include).

export function buildDiffusionSpec(opts: {
  modelId: string;
  modelType: string;
  trustRemoteCode?: boolean;
  modelOffload?: boolean;
  groupOffload?: boolean;
}): string {
  const finalModelId = opts.modelId || "segmind/tiny-sd";
  const config: Record<string, string | boolean> = { model_type: opts.modelType };
  if (opts.trustRemoteCode) config.trust_remote_code = true;
  if (opts.modelOffload) config.model_offload = true;
  if (opts.groupOffload) config.group_offload = true;
  const spec = {
    model_id: finalModelId,
    engine: "inferia-diffusion",
    image: "docker.io/inferiaai/inferiadiffusion:latest",
    port: 8000,
    host: "0.0.0.0",
    min_vram: 8,
    gpu: true,
    config,
    expose: [{
      port: 8000,
      type: "http",
      health_checks: [{ path: "/health", type: "http", method: "GET", expected_status: 200 }],
    }],
  };
  return JSON.stringify(spec, null, 4);
}
