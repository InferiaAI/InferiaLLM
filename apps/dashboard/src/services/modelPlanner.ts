/**
 * modelPlanner — centralized model calculation engine.
 *
 * All model-vs-hardware planning, job spec generation, and
 * compatibility projection data live here so the dashboard
 * imports pure functions instead of duplicating logic.
 */

import {
  calculateCompatibility,
  projectCompatibilityPerformance,
  GPU_SPECS,
  type CompatibilityResult,
  type ExternalModel,
} from "@/services/gpuCompatibility"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface JobSpecInput {
  selectedEngine: string
  modelId: string
  modelType: string
  hfToken: string
  batchSize: string
  maxBatchTokens: string
  pooling: string
  requiredCpu: string
  requiredRam: string
  gpuEnabled: boolean
  trustRemoteCode: boolean
  modelOffload: boolean
  groupOffload: boolean
  enableDisagg: boolean
  prefillReplicas: string
  decodeReplicas: string
  prefillGpuCount: string
  attentionBackend: string
  samplingBackend: string
  memFractionStatic: string
  chunkedPrefillSize: string
  maxRunningRequests: string
}

export interface PlannedDefaults {
  maxModelLen?: string
  gpuUtil?: string
  enforceEager?: boolean
  dtype?: string
  enableDisagg?: boolean
  prefillGpuCount?: string
  prefillReplicas?: string
  decodeReplicas?: string
}

export interface ProjectionDataPoint {
  concurrency: number
  ttftSeconds: number
  referenceTtftSeconds: number
  totalTps: number
  tpsPerUser: number
  tpsPerUserLabel: string
}

// ---------------------------------------------------------------------------
// buildJobSpec — pure function (moved from NewDeployment.tsx)
// ---------------------------------------------------------------------------

export function buildJobSpec(input: JobSpecInput): string {
  const {
    selectedEngine, modelId, modelType, hfToken,
    batchSize, maxBatchTokens, pooling,
    requiredCpu, requiredRam, gpuEnabled,
    trustRemoteCode, modelOffload, groupOffload,
    enableDisagg, prefillReplicas, decodeReplicas, prefillGpuCount,
    attentionBackend, samplingBackend, memFractionStatic,
    chunkedPrefillSize, maxRunningRequests,
  } = input

  const isDisagg = (selectedEngine === "vllm" || selectedEngine === "sglang") && enableDisagg

  if (isDisagg) {
    const finalModelId = modelId || "meta-llama/Meta-Llama-3-8B-Instruct"
    const recipe = selectedEngine === "vllm" ? "vllm-prefill-decode" : "sglang-prefill-decode"
    const pGpu = parseInt(prefillGpuCount) || 1
    const dGpu = 1
    const prefillIndices = Array.from({ length: pGpu }, (_, i) => i)
    const decodeIndices = Array.from({ length: dGpu }, (_, i) => pGpu + i)
    const spec: Record<string, unknown> = {
      model_id: finalModelId,
      engine: selectedEngine,
      recipe,
      prefill_replicas: parseInt(prefillReplicas) || 1,
      decode_replicas: parseInt(decodeReplicas) || 1,
      prefill_gpu_indices: prefillIndices,
      decode_gpu_indices: decodeIndices,
      gpu: true,
      expose: [{ port: 9000, type: "http" }],
    }
    if (selectedEngine === "sglang") {
      spec.attention_backend = attentionBackend
      spec.sampling_backend = samplingBackend
      spec.mem_fraction_static = parseFloat(memFractionStatic) || 0.88
      spec.chunked_prefill_size = parseInt(chunkedPrefillSize) || 8192
      spec.max_running_requests = parseInt(maxRunningRequests) || 64
    }
    return JSON.stringify(spec, null, 4)
  }

  if (selectedEngine === "vllm" && modelType === "inference") {
    const finalModelId = modelId || "meta-llama/Meta-Llama-3-8B-Instruct"
    const spec = {
      model_id: finalModelId,
      engine: "vllm",
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
    }
    return JSON.stringify(spec, null, 4)
  }

  if (selectedEngine === "sglang" && modelType === "inference") {
    const finalModelId = modelId || "meta-llama/Meta-Llama-3-8B-Instruct"
    const spec: Record<string, unknown> = {
      model_id: finalModelId,
      engine: "sglang",
      gpu: true,
      expose: [{
        port: 30000,
        health_checks: [{ path: "/health", type: "http", method: "GET", expected_status: 200 }],
      }],
      shm_size: 34359738368,
    }
    if (attentionBackend) spec.attention_backend = attentionBackend
    if (samplingBackend) spec.sampling_backend = samplingBackend
    if (parseFloat(memFractionStatic)) spec.mem_fraction_static = parseFloat(memFractionStatic)
    if (parseInt(chunkedPrefillSize)) spec.chunked_prefill_size = parseInt(chunkedPrefillSize)
    if (parseInt(maxRunningRequests)) spec.max_running_requests = parseInt(maxRunningRequests)
    return JSON.stringify(spec, null, 4)
  }

  if (selectedEngine === "ollama") {
    const finalModelId = modelId || "llama3:8b"
    return JSON.stringify({
      model_id: finalModelId,
      engine: "ollama",
      image: "ollama/ollama:latest",
      cmd: ["serve"],
      expose: [{ port: 11434, type: "http" }],
      gpu: true,
    }, null, 4)
  }

  if (selectedEngine === "infinity") {
    const finalModelId = modelId || "sentence-transformers/all-MiniLM-L6-v2"
    return JSON.stringify({
      model_id: finalModelId,
      engine: "infinity",
      image: "michaelf34/infinity:latest",
      port: 7997,
      batch_size: parseInt(batchSize) || 32,
      gpu: gpuEnabled,
      required_cpu: parseInt(requiredCpu) || 2,
      required_ram: parseInt(requiredRam) || 4096,
      env: {
        INFINITY_MODEL_ID: finalModelId,
        INFINITY_PORT: "7997",
        ...(hfToken ? { HF_TOKEN: hfToken } : {}),
      },
      expose: [{
        port: 7997,
        type: "http",
        health_checks: [{ path: "/health", type: "http", method: "GET", expected_status: 200 }],
      }],
    }, null, 4)
  }

  if (selectedEngine === "tei") {
    const finalModelId = modelId || "sentence-transformers/all-MiniLM-L6-v2"
    return JSON.stringify({
      model_id: finalModelId,
      engine: "tei",
      image: "ghcr.io/huggingface/text-embeddings-inference:latest",
      port: 8080,
      max_batch_tokens: parseInt(maxBatchTokens) || 16384,
      pooling: pooling || "cls",
      gpu: gpuEnabled,
      required_cpu: parseInt(requiredCpu) || 2,
      required_ram: parseInt(requiredRam) || 4096,
      env: hfToken ? { HF_TOKEN: hfToken } : {},
      expose: [{
        port: 8080,
        type: "http",
        health_checks: [{ path: "/health", type: "http", method: "GET", expected_status: 200 }],
      }],
    }, null, 4)
  }

  if (selectedEngine === "inferia-diffusion") {
    const finalModelId = modelId || "segmind/tiny-sd"
    const spec: Record<string, unknown> = {
      model_id: finalModelId,
      engine: "inferia-diffusion",
      image: "docker.io/inferiaai/inferiadiffusion:latest",
      port: 8080,
      host: "0.0.0.0",
      min_vram: 8,
      gpu: true,
      env: hfToken ? { HF_TOKEN: hfToken } : {},
      expose: [{
        port: 8080,
        type: "http",
        health_checks: [{ path: "/health", type: "http", method: "GET", expected_status: 200 }],
      }],
    }
    if (trustRemoteCode) spec["trust_remote_code"] = true
    if (modelOffload) spec["model_offload"] = true
    if (groupOffload) spec["group_offload"] = true
    return JSON.stringify(spec, null, 4)
  }

  if (selectedEngine === "pytorch") {
    return JSON.stringify({
      image: "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
      cmd: ["sleep", "infinity"],
      gpu: true,
    }, null, 4)
  }

  return ""
}

// ---------------------------------------------------------------------------
// computePlannedDefaults  —  intelligent planner (Task 1)
// ---------------------------------------------------------------------------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Poolish = any

export function computePlannedDefaults(
  pool: Poolish,
  modelId: string | null,
  engine: string,
  compatibility: CompatibilityResult | null,
): PlannedDefaults | null {
  if (!pool || !modelId || !compatibility) return null

  const defaults: PlannedDefaults = {}

  // Apply recommended engine config from compatibility analysis
  const cfg = compatibility.recommendedVllmConfig
  if (cfg) {
    defaults.maxModelLen = cfg.maxModelLen.toString()
    defaults.gpuUtil = cfg.gpuMemoryUtilization.toString()
    defaults.enforceEager = cfg.enforceEager
    defaults.dtype = cfg.dtype
  }

  // Auto-apply disaggregated split for engines that support it
  const supportsDisagg = engine === "vllm" || engine === "sglang"
  const gpuCount = pool.gpu_count || 0

  if (supportsDisagg && gpuCount > 2) {
    const prefillGpu = Math.max(1, Math.floor(gpuCount / 2))
    const decodeGpu = gpuCount - prefillGpu
    defaults.enableDisagg = true
    defaults.prefillGpuCount = prefillGpu.toString()
    defaults.prefillReplicas = prefillGpu.toString()
    defaults.decodeReplicas = decodeGpu.toString()
  }

  return defaults
}

// ---------------------------------------------------------------------------
// computeEffectiveGpuCount  —  resolve GPU count for the deploy payload
// ---------------------------------------------------------------------------

export function computeEffectiveGpuCount(
  engine: string,
  enableDisagg: boolean,
  prefillGpuCount: string,
  poolGpuCount: number,
): number {
  if (!enableDisagg) return 1
  if (engine !== "vllm" && engine !== "sglang") return 1
  const pGpu = parseInt(prefillGpuCount) || 1
  const total = pGpu + 1
  // Clamp to pool capacity
  return Math.min(total, poolGpuCount || total)
}

// ---------------------------------------------------------------------------
// prepareProjectionData  —  chart data prep (moved from
//                           CompatibilityProjectionChart.tsx)
// ---------------------------------------------------------------------------

function formatTpsPerUser(value: number): string {
  if (value >= 100) return value.toFixed(0)
  if (value >= 10) return value.toFixed(1)
  return value.toFixed(2)
}

export function prepareProjectionData(
  compatibility: CompatibilityResult,
  inputTokens: number = 200,
  outputTokens: number = 200,
): ProjectionDataPoint[] {
  return projectCompatibilityPerformance(compatibility, {
    inputTokens,
    outputTokens,
  }).map((point) => ({
    ...point,
    tpsPerUserLabel: formatTpsPerUser(point.tpsPerUser),
  }))
}

// ---------------------------------------------------------------------------
// resolveGpuSpecs  —  derive GPU VRAM/bandwidth from pool metadata
// ---------------------------------------------------------------------------

export interface ResolvedGpuSpecs {
  poolGpuCount: number
  gpuKey: string
  gpuSpecKey: string | undefined
  singleGpuVram: number
  aggregatedVram: number | undefined
  baseBandwidth: number | undefined
  aggregatedBandwidth: number | undefined
}

export function resolveGpuSpecs(pool: Poolish): ResolvedGpuSpecs {
  const poolGpuCount = pool?.gpu_count || 1
  const gpuKey = (pool?.allowed_gpu_types?.[0] || "").toUpperCase().replace(/[\s-]/g, "")
  const gpuSpecKey = Object.keys(GPU_SPECS).find((k) => {
    const nk = k.toUpperCase().replace(/[\s-]/g, "")
    return gpuKey.includes(nk) || nk.includes(gpuKey)
  })
  const singleGpuVram = pool?.gpu_specs?.[0]?.vram || (gpuSpecKey ? GPU_SPECS[gpuSpecKey]?.vram : 0) || 0
  const aggregatedVram = poolGpuCount > 1 ? singleGpuVram * poolGpuCount : undefined
  const baseBandwidth = gpuSpecKey ? GPU_SPECS[gpuSpecKey]?.bandwidth : undefined
  const aggregatedBandwidth = poolGpuCount > 1 && baseBandwidth
    ? baseBandwidth * poolGpuCount * 0.85
    : undefined
  return { poolGpuCount, gpuKey, gpuSpecKey, singleGpuVram, aggregatedVram, baseBandwidth, aggregatedBandwidth }
}

// ---------------------------------------------------------------------------
// computeCompatibility — thin wrapper that delegates to gpuCompatibility's
//                        calculateCompatibility with resolved pool specs
// ---------------------------------------------------------------------------

export function computeCompatibility(
  modelId: string,
  pool: Poolish,
  engine: string,
  quantization: string | undefined,
  dtype: string | undefined,
  hfContextLength: number | undefined,
  hfHiddenSize: number | undefined,
  hfNumLayers: number | undefined,
  hfNumAttentionHeads: number | undefined,
  hfNumKeyValueHeads: number | undefined,
  externalRegistry: ExternalModel[] | undefined,
): CompatibilityResult | null {
  const validEngine = engine === "vllm" || engine === "sglang" || engine === "ollama"
  if (!pool || !modelId || !validEngine) return null

  const specs = resolveGpuSpecs(pool)
  return calculateCompatibility(
    modelId,
    pool.allowed_gpu_types?.[0] || "GENERIC-GPU",
    quantization || dtype,
    {
      vram: specs.aggregatedVram,
      bandwidth: specs.aggregatedBandwidth,
      contextLength: hfContextLength,
      hiddenSize: hfHiddenSize,
      numLayers: hfNumLayers,
      numAttentionHeads: hfNumAttentionHeads,
      numKeyValueHeads: hfNumKeyValueHeads,
    },
    externalRegistry,
  )
}
