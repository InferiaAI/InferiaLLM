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

// vLLM Omni is an omni-modal vLLM server (image/video generation). The worker
// recipe ("vllm-omni") shares vLLM's CLI surface plus an --omni flag and serves
// /health on port 8091. It reads `trust_remote_code` from config (the
// diffusion-only model_offload/group_offload knobs are ignored by this recipe).
export function buildVllmOmniSpec(opts: {
  modelId: string;
  modelType: string;
  trustRemoteCode?: boolean;
}): string {
  const finalModelId = opts.modelId || "Qwen/Qwen2.5-Omni-7B";
  const config: Record<string, string | boolean> = { model_type: opts.modelType };
  if (opts.trustRemoteCode) config.trust_remote_code = true;
  const spec = {
    model_id: finalModelId,
    engine: "vllm-omni",
    image: "docker.io/vllm/vllm-omni:v0.23.0",
    port: 8091,
    host: "0.0.0.0",
    gpu: true,
    config,
    expose: [{
      port: 8091,
      type: "http",
      health_checks: [{ path: "/health", type: "http", method: "GET", expected_status: 200 }],
    }],
  };
  return JSON.stringify(spec, null, 4);
}

// The vLLM settings saved on a deployment. A blank field, "auto" or an unticked
// box is left out, so the worker's GPU profile or the engine decides it.
export function buildVllmSettings(opts: {
  maxModelLen?: string;
  gpuUtil?: string;
  dtype?: string;
  quantization?: string;
  enforceEager?: boolean;
}): Record<string, string | number | boolean> {
  const settings: Record<string, string | number | boolean> = {};
  const maxModelLen = parseInt(opts.maxModelLen ?? "", 10);
  if (maxModelLen > 0) settings.max_model_len = maxModelLen;
  const gpuUtil = parseFloat(opts.gpuUtil ?? "");
  if (gpuUtil > 0 && gpuUtil <= 1) settings.gpu_memory_utilization = gpuUtil;
  if (opts.dtype && opts.dtype !== "auto") settings.dtype = opts.dtype;
  if (opts.quantization) settings.quantization = opts.quantization;
  if (opts.enforceEager) settings.enforce_eager = true;
  return settings;
}

// The job spec for vLLM and SGLang. Only vLLM takes the runtime settings: no
// backend path starts SGLang, so they'd have nowhere to go.
export function buildVllmJobSpec(opts: {
  modelId: string;
  engine: string;
  maxModelLen?: string;
  gpuUtil?: string;
  dtype?: string;
  quantization?: string;
  enforceEager?: boolean;
  prefillReplicas: string;
  decodeReplicas: string;
  prefillGpuIndices?: string;
  decodeGpuIndices?: string;
}): string {
  const finalModelId = opts.modelId || "meta-llama/Meta-Llama-3-8B-Instruct";
  const spec: Record<string, unknown> = {
    model_id: finalModelId,
    engine: opts.engine,
    expose: [{
      port: 9000,
      health_checks: [{
        body: JSON.stringify({
          model: finalModelId,
          messages: [{ role: "user", content: "Respond with a single word: Ready" }],
          stream: false,
        }),
        path: "/v1/chat/completions",
        type: "http",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        continuous: false,
        expected_status: 200,
      }],
    }],
    gpu: true,
  };
  if (opts.engine === "vllm") Object.assign(spec, buildVllmSettings(opts));

  if (opts.prefillReplicas !== "0" || opts.decodeReplicas !== "0") {
    spec.prefill_replicas = parseInt(opts.prefillReplicas) || 0;
    spec.decode_replicas = parseInt(opts.decodeReplicas) || 0;
    if (opts.prefillGpuIndices) spec.prefill_gpu_indices = opts.prefillGpuIndices.split(",").map(Number);
    if (opts.decodeGpuIndices) spec.decode_gpu_indices = opts.decodeGpuIndices.split(",").map(Number);
  }
  return JSON.stringify(spec, null, 4);
}
