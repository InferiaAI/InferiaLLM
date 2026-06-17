import { calculateCompatibility, GPU_SPECS, type FitLevel, type CompatibilityResult, type ExternalModel } from "./gpuCompatibility";

// ---- Types ----

export interface PoolGpuResources {
  gpuCount: number;
  gpuKey: string;
  gpuSpecKey: string | undefined;
  singleGpuVram: number;
  aggregatedVram: number | undefined;
  singleGpuBandwidth: number | undefined;
  aggregatedBandwidth: number | undefined;
}

export interface ModelArchitecture {
  contextLength?: number;
  hiddenSize?: number;
  numLayers?: number;
  numAttentionHeads?: number;
  numKeyValueHeads?: number;
}

export interface LlmfitTtftResult {
  ttftMs: number;
  prefillMs: number;
  decodeFirstMs: number;
  prefillTps: number;
}

export interface PoolCompatibilityWithFit extends CompatibilityResult {
  ttft: LlmfitTtftResult;
}

// ---- LLMFit Server Config ----

export function getLlmfitBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const rc = (window as unknown as { __RUNTIME_CONFIG__?: { LLMFIT_SERVER_URL?: string } }).__RUNTIME_CONFIG__;
    if (rc?.LLMFIT_SERVER_URL) return rc.LLMFIT_SERVER_URL;
  }
  return '/api/v1/llmfit';
}

interface LlmfitModelFit {
  name: string;
  estimated_tps: number;
  fit_level: string;
  fit_label: string;
  memory_required_gb: number;
  memory_available_gb: number;
  utilization_pct: number;
  runtime: string;
  runtime_label: string;
  context_length: number;
  params_b: number;
  score: number;
  score_components: { quality: number; speed: number; fit: number; context: number };
  notes: string[];
}

// ---- GPU Resource Resolution ----

export function resolvePoolGpuResources(pool: any): PoolGpuResources {
  const gpuCount = pool?.gpu_count || 1;
  const gpuKey = (pool?.allowed_gpu_types?.[0] || "").toUpperCase().replace(/[\s-]/g, "") || "";
  const gpuSpecKey = Object.keys(GPU_SPECS).find(k => {
    const nk = k.toUpperCase().replace(/[\s-]/g, "");
    return gpuKey.includes(nk) || nk.includes(gpuKey);
  });
  const singleGpuVram = pool?.gpu_specs?.[0]?.vram || (gpuSpecKey ? GPU_SPECS[gpuSpecKey]?.vram : 0) || 0;
  const aggregatedVram = gpuCount > 1 ? singleGpuVram * gpuCount : undefined;
  const singleGpuBandwidth = gpuSpecKey ? GPU_SPECS[gpuSpecKey]?.bandwidth : undefined;
  const aggregatedBandwidth = (gpuCount > 1 && singleGpuBandwidth)
    ? singleGpuBandwidth * gpuCount * 0.85
    : undefined;
  return { gpuCount, gpuKey, gpuSpecKey, singleGpuVram, aggregatedVram, singleGpuBandwidth, aggregatedBandwidth };
}

// ---- Model Architecture Extraction ----

export function extractHfArchitecture(hfConfig: any): ModelArchitecture {
  if (!hfConfig) return {};
  return {
    contextLength: hfConfig.max_position_embeddings || hfConfig.seq_length || hfConfig.max_sequence_length,
    hiddenSize: hfConfig.hidden_size,
    numLayers: hfConfig.num_hidden_layers,
    numAttentionHeads: hfConfig.num_attention_heads,
    numKeyValueHeads: hfConfig.num_key_value_heads,
  };
}

// ---- Compatibility Calculation for a Pool ----

export function calculatePoolCompatibility(
  modelId: string,
  pool: any,
  hfConfig: any,
  quantization: string,
  dtype: string,
  externalRegistry?: ExternalModel[],
): CompatibilityResult | null {
  if (!pool || !modelId) return null;
  const resources = resolvePoolGpuResources(pool);
  const arch = extractHfArchitecture(hfConfig);
  return calculateCompatibility(
    modelId,
    pool.allowed_gpu_types?.[0] || "GENERIC-GPU",
    quantization || dtype,
    {
      vram: resources.aggregatedVram,
      bandwidth: resources.aggregatedBandwidth,
      contextLength: arch.contextLength,
      hiddenSize: arch.hiddenSize,
      numLayers: arch.numLayers,
      numAttentionHeads: arch.numAttentionHeads,
      numKeyValueHeads: arch.numKeyValueHeads,
    },
    externalRegistry,
  );
}

// ---- Fit Color Utility ----

export function getFitColor(level: FitLevel): string {
  switch (level) {
    case "Perfect": return "text-ember-500 bg-ember-500/10 border-ember-500/20";
    case "Good": return "text-blue-500 bg-blue-500/10 border-blue-500/20";
    case "Marginal": return "text-amber-500 bg-amber-500/10 border-amber-500/20";
    case "TooTight": return "text-rose-500 bg-rose-500/10 border-rose-500/20";
    default: return "text-muted-foreground bg-muted-foreground/10 border-muted-foreground/20";
  }
}

// =====================================================================
// LLMFit Server Integration
// =====================================================================

function extractModelName(modelId: string): string {
  return modelId.split('/').pop() || modelId;
}

function extractParamsFromModelId(modelId: string): number {
  const match = modelId.match(/(\d+\.?\d*)\s*[bB]/);
  return match ? parseFloat(match[1]) : 7;
}

function mapLlmfitFitLevel(level: string): FitLevel {
  switch (level) {
    case "perfect": return "Perfect";
    case "good": return "Good";
    case "marginal": return "Marginal";
    case "too_tight": return "TooTight";
    default: return "Marginal";
  }
}

function mapRuntimeToEngine(runtime: string): string | undefined {
  switch (runtime) {
    case "vllm": return "vllm";
    case "llamacpp": return "llamacpp";
    default: return undefined;
  }
}

export async function checkLlmfitHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${getLlmfitBaseUrl()}/health`, { signal: AbortSignal.timeout(2000) });
    return response.ok;
  } catch {
    return false;
  }
}

export async function queryLlmfitModelFit(
  modelId: string,
  vramGb: number,
  cpuCores: number,
  ramGb: number,
  engineType?: string,
): Promise<LlmfitModelFit | null> {
  try {
    const searchTerm = extractModelName(modelId);
    const params = new URLSearchParams({
      ram_gb: String(Math.max(ramGb, 8)),
      vram_gb: String(Math.max(vramGb, 1)),
      cpu_cores: String(Math.max(cpuCores, 1)),
      search: searchTerm,
      limit: "10",
      sort: "score",
    });
    const llmfitRuntime = mapRuntimeToEngine(engineType || "");
    if (llmfitRuntime) params.set("force_runtime", llmfitRuntime);

    const response = await fetch(
      `${getLlmfitBaseUrl()}/api/v1/models?${params}`,
      { signal: AbortSignal.timeout(5000) },
    );
    if (!response.ok) return null;

    const data = await response.json();
    const models: LlmfitModelFit[] = data?.models;
    return models?.[0] || null;
  } catch {
    return null;
  }
}

/**
 * Simplified TTFT calculation assuming Q4 quantization.
 *
 * TTFT = T_prefill + T_decode1
 *   T_decode1 = 1000 / estimated_tps (ms)
 *   T_prefill = (prompt_length / R_prefill) * 1000 (ms)
 *
 * Prefill throughput (R_prefill):
 *   R_prefill = 2000 * (7 / params_b)^0.6 * S_hw * M_quant * (1 / C_scale)
 *     S_hw   = bandwidth / 360  (scaled against RTX 3060 reference)
 *     M_quant = 2.32  (Q4 fixed: 1.0 + (2.1 - 1.0) * 1.2)
 *     C_scale = tiered context-length scaling factor
 */
export function calculateTtft(
  estimatedTps: number,
  paramsB: number,
  bandwidth: number,
  contextLength: number,
  inputTokens: number = 200,
): LlmfitTtftResult {
  const activeParams = Math.max(paramsB, 0.5);
  const decodeFirstMs = 1000 / Math.max(estimatedTps, 0.1);

  const refBandwidth = 360;
  const S_hw = bandwidth / refBandwidth;
  const M_size = Math.pow(7 / activeParams, 0.6);
  const M_quant = 2.32;

  const seqLen = Math.max(inputTokens, 1);
  let C_scale: number;
  if (seqLen < 1024) {
    C_scale = Math.pow(seqLen / 2048, 0.75);
  } else if (seqLen < 8192) {
    C_scale = Math.pow(seqLen / 2048, 0.85);
  } else {
    C_scale = Math.pow(seqLen / 2048, 0.92);
  }

  const prefillTps = 2000 * M_size * M_quant * S_hw * (1 / Math.max(C_scale, 0.1));
  const prefillMs = (seqLen / Math.max(prefillTps, 1)) * 1000;

  return {
    ttftMs: prefillMs + decodeFirstMs,
    prefillMs,
    decodeFirstMs,
    prefillTps,
  };
}

export async function calculatePoolCompatibilityWithFit(
  modelId: string,
  pool: any,
  hfConfig: any,
  quantization: string,
  dtype: string,
  externalRegistry?: ExternalModel[],
  engineType?: string,
): Promise<PoolCompatibilityWithFit | null> {
  if (!pool || !modelId) return null;

  const resources = resolvePoolGpuResources(pool);
  const bandwidth = resources.aggregatedBandwidth || resources.singleGpuBandwidth || 300;
  const vramGb = resources.aggregatedVram || resources.singleGpuVram;
  const arch = extractHfArchitecture(hfConfig);

  const llmfitUp = await checkLlmfitHealth();

  if (llmfitUp) {
    const fit = await queryLlmfitModelFit(
      modelId,
      vramGb,
      pool?.cpu_cores || 8,
      pool?.ram_gb || 64,
      engineType,
    );

    if (fit) {
      const baseResult = calculateCompatibility(
        modelId,
        pool.allowed_gpu_types?.[0] || "GENERIC-GPU",
        quantization || dtype,
        {
          vram: resources.aggregatedVram,
          bandwidth: resources.aggregatedBandwidth,
          contextLength: arch.contextLength,
          hiddenSize: arch.hiddenSize,
          numLayers: arch.numLayers,
        },
        externalRegistry,
      );

      const mapped: CompatibilityResult = {
        ...baseResult,
        estimatedTps: fit.estimated_tps,
        fitLevel: mapLlmfitFitLevel(fit.fit_level),
        requiredVram: fit.memory_required_gb || baseResult.requiredVram,
        availableVram: fit.memory_available_gb || baseResult.availableVram,
        score: Math.round(fit.score),
        details: {
          qualityScore: fit.score_components?.quality ?? baseResult.details.qualityScore,
          speedScore: fit.score_components?.speed ?? baseResult.details.speedScore,
          fitScore: fit.score_components?.fit ?? baseResult.details.fitScore,
          contextScore: fit.score_components?.context ?? baseResult.details.contextScore,
        },
      };

      const paramsB = fit.params_b || extractParamsFromModelId(modelId);
      const ttft = calculateTtft(
        mapped.estimatedTps,
        paramsB,
        bandwidth,
        fit.context_length || arch.contextLength || 4096,
      );

      return { ...mapped, ttft };
    }
  }

  const base = calculatePoolCompatibility(modelId, pool, hfConfig, quantization, dtype, externalRegistry);
  if (!base) return null;

  const paramsB = extractParamsFromModelId(modelId);
  const ttft = calculateTtft(base.estimatedTps, paramsB, bandwidth, arch.contextLength || 4096);

  return { ...base, ttft };
}
