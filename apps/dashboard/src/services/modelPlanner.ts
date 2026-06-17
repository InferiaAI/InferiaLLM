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
