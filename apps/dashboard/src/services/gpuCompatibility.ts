/**
 * GPU and Model Compatibility Service
 * 
 * Deeply integrated formulas from AlexsJones/llmfit
 */

export interface GpuSpec {
    name: string;
    vram: number; // GB
    bandwidth: number; // GB/s
}

export const GPU_SPECS: Record<string, GpuSpec> = {
    // High-End Enterprise & Workspace
    "H100": { name: "NVIDIA H100", vram: 80, bandwidth: 3350 },
    "A100-80GB": { name: "NVIDIA A100 80GB", vram: 80, bandwidth: 1935 },
    "A100-40GB": { name: "NVIDIA A100 40GB", vram: 40, bandwidth: 1555 },
    "RTX-PRO-6000-BLACKWELL": { name: "RTX 6000 Blackwell", vram: 96, bandwidth: 2200 },
    "RTX-PRO-6000": { name: "RTX Pro 6000", vram: 96, bandwidth: 960 },
    "RTX-6000-ADA": { name: "RTX 6000 Ada", vram: 48, bandwidth: 960 },
    "A40": { name: "NVIDIA A40", vram: 48, bandwidth: 696 },
    "RTX-A6000": { name: "RTX A6000", vram: 48, bandwidth: 768 },
    "A5000": { name: "RTX A5000", vram: 24, bandwidth: 768 },
    "A4000": { name: "RTX A4000", vram: 16, bandwidth: 448 },
    "RTX-4000-SFF-ADA": { name: "RTX 4000 SFF Ada", vram: 16, bandwidth: 280 },

    // RTX 50-Series (Blackwell Consumer)
    "RTX-5090": { name: "RTX 5090", vram: 32, bandwidth: 2000 },
    "RTX-5080": { name: "RTX 5080", vram: 16, bandwidth: 1024 },
    "RTX-5070": { name: "RTX 5070", vram: 12, bandwidth: 504 },

    // RTX 40-Series (Ada Lovelace)
    "RTX-4090": { name: "RTX 4090", vram: 24, bandwidth: 1008 },
    "RTX-4080": { name: "RTX 4080", vram: 16, bandwidth: 716 },
    "RTX-4070": { name: "RTX 4070", vram: 12, bandwidth: 504 },
    "RTX-4060": { name: "RTX 4060", vram: 8, bandwidth: 272 },

    // RTX 30-Series (Ampere)
    "RTX-3090": { name: "RTX 3090", vram: 24, bandwidth: 936 },
    "RTX-3080": { name: "RTX 3080", vram: 10, bandwidth: 760 },
    "RTX-3070": { name: "RTX 3070", vram: 8, bandwidth: 448 },
    "RTX-3060-TI": { name: "RTX 3060 Ti", vram: 8, bandwidth: 448 },
    "RTX-3060": { name: "RTX 3060", vram: 12, bandwidth: 360 },

    // Budget & Legacy
    "T4": { name: "NVIDIA T4", vram: 16, bandwidth: 320 },
    "L4": { name: "NVIDIA L4", vram: 24, bandwidth: 300 },
    "V100": { name: "NVIDIA V100", vram: 32, bandwidth: 900 },
    "GENERIC-GPU": { name: "Generic GPU", vram: 16, bandwidth: 300 },
};

export interface ModelProfile {
    id: string;
    name: string;
    parameters: number; // Billions
    activeParameters?: number; // For MoE models
    isMoE?: boolean;
    contextLength: number;
    hiddenSize?: number;
    numLayers?: number;
}

export const MODEL_PROFILES: Record<string, ModelProfile> = {
    "meta-llama/Meta-Llama-3-8B": { id: "meta-llama/Meta-Llama-3-8B", name: "Llama 3 8B", parameters: 8, contextLength: 8192, hiddenSize: 4096, numLayers: 32 },
    "meta-llama/Meta-Llama-3-70B": { id: "meta-llama/Meta-Llama-3-70B", name: "Llama 3 70B", parameters: 70, contextLength: 8192, hiddenSize: 8192, numLayers: 80 },
    "mistralai/Mistral-7B-v0.1": { id: "mistralai/Mistral-7B-v0.1", name: "Mistral 7B", parameters: 7, contextLength: 32768, hiddenSize: 4096, numLayers: 32 },
    "mistralai/Mixtral-8x7B-v0.1": { id: "mistralai/Mixtral-8x7B-v0.1", name: "Mixtral 8x7B", parameters: 46.7, activeParameters: 12.9, isMoE: true, contextLength: 32768, hiddenSize: 4096, numLayers: 32 },
    "google/gemma-7b": { id: "google/gemma-7b", name: "Gemma 7B", parameters: 8.5, contextLength: 8192, hiddenSize: 3072, numLayers: 28 },
    "deepseek-ai/DeepSeek-V3": { id: "deepseek-ai/DeepSeek-V3", name: "DeepSeek V3", parameters: 671, activeParameters: 37, isMoE: true, contextLength: 32768, hiddenSize: 7168, numLayers: 61 },
};

/**
 * Weights per quantization level (BPW / 8)
 */
export const QUANTIZATION_WEIGHTS = {
    "float16": 2.0,
    "bfloat16": 2.0,
    "float32": 4.0,
    "q8_0": 1.05,
    "q6_k": 0.82,
    "q5_k_m": 0.70,
    "q4_k_m": 0.58, // Standard 4-bit
    "q4_0": 0.58,
    "q3_k_m": 0.46,
    "q2_k": 0.37,
};

export type FitLevel = "Perfect" | "Good" | "Marginal" | "TooTight";

export interface CompatibilityResult {
    fitLevel: FitLevel;
    requiredVram: number;
    availableVram: number;
    isCompatible: boolean;
    score: number; // 0-100 (Multi-dimensional)
    estimatedTps: number;
    reason: string;
    details: {
        qualityScore: number;
        speedScore: number;
        fitScore: number;
        contextScore: number;
    }
}

export interface ExternalModel {
    name: string;
    provider: string;
    parameter_count: string;
    parameters_raw: number;
    min_ram_gb: number;
    recommended_ram_gb: number;
    min_vram_gb: number;
    quantization: string;
    context_length: number;
    use_case: string;
    pipeline_tag: string;
    architecture: string;
    hf_downloads: number;
    hf_likes: number;
    release_date: string;
    _discovered: boolean;
}

const EXTERNAL_REGISTRY_URL = "https://raw.githubusercontent.com/AlexsJones/llmfit/refs/heads/main/data/hf_models.json";
let cachedRegistry: ExternalModel[] | null = null;

export async function fetchExternalRegistry(): Promise<ExternalModel[]> {
    if (cachedRegistry) return cachedRegistry;
    try {
        const response = await fetch(EXTERNAL_REGISTRY_URL);
        if (!response.ok) throw new Error("Failed to fetch model registry");
        cachedRegistry = await response.json();
        return cachedRegistry || [];
    } catch (error) {
        console.error("Registry fetch error:", error);
        return [];
    }
}

/**
 * Calculate required VRAM for KV Cache
 * Formula: 2 * num_layers * hidden_size * context_length * bytes_per_element
 */
function calculateKVCacheGB(hiddenSize: number, numLayers: number, contextLength: number): number {
    if (!hiddenSize || !numLayers) return 1.0; // Default 1GB floor
    const elements = 2 * numLayers * hiddenSize * contextLength;
    return (elements * 2) / (1024 ** 3); // Assuming 16-bit cache
}

/**
 * Calculate compatibility between a model and a GPU
 */
export function calculateCompatibility(
    modelId: string,
    gpuId: string,
    quantization?: string,
    overrides?: { parameters?: number; vram?: number; bandwidth?: number; contextLength?: number },
    externalRegistry?: ExternalModel[]
): CompatibilityResult {
    // 1. Get Model Data
    const profile = MODEL_PROFILES[modelId] || MODEL_PROFILES[Object.keys(MODEL_PROFILES).find(k => modelId.includes(k)) || ""];
    const externalModel = externalRegistry?.find(m => m.name === modelId);

    // 2. Determine Parameters and Base Requirements
    const isMoE = profile?.isMoE || modelId.toLowerCase().includes("mixtral") || modelId.toLowerCase().includes("moe") || externalModel?.architecture === "mixtral";
    const totalParams = overrides?.parameters || profile?.parameters || (externalModel?.parameters_raw ? externalModel.parameters_raw / 1e9 : 7);
    const activeParams = isMoE ? (profile?.activeParameters || totalParams * 0.25) : totalParams;
    const contextLength = overrides?.contextLength || profile?.contextLength || externalModel?.context_length || 4096;

    // 3. Get GPU Data
    const normalizedId = gpuId.toUpperCase().replace(/[\s-]/g, "");
    const matchKey = Object.keys(GPU_SPECS).find(k => {
        const normalizedKey = k.toUpperCase().replace(/[\s-]/g, "");
        return normalizedId.includes(normalizedKey) || normalizedKey.includes(normalizedId);
    }) || "GENERIC-GPU";
    const gpuSpecFromRegistry = GPU_SPECS[matchKey];
    const gpuSpec = {
        name: gpuSpecFromRegistry.name,
        vram: (overrides?.vram && overrides.vram > 0) ? overrides.vram : gpuSpecFromRegistry.vram,
        bandwidth: (overrides?.bandwidth && overrides.bandwidth > 0) ? overrides.bandwidth : gpuSpecFromRegistry.bandwidth
    };

    // 4. Advanced Memory Calculation
    const bytesPerParam = (QUANTIZATION_WEIGHTS as any)[quantization || "q4_k_m"] || 0.6;
    const kvCacheGB = calculateKVCacheGB(profile?.hiddenSize || 4096, profile?.numLayers || 32, contextLength);

    // Effective VRAM Calculation (MoE Aware)
    // If we can offload experts, we only need VRAM for active params + KV cache
    const effectiveModelSizeGB = activeParams * bytesPerParam;
    const totalModelSizeGB = totalParams * bytesPerParam;

    // requiredVram assumes full model in VRAM for high performance (Perfect Fit)
    // marginalRequiredVram assumes expert offloading (Good Fit)
    const idealRequiredVram = totalModelSizeGB + kvCacheGB;
    const offloadRequiredVram = effectiveModelSizeGB + kvCacheGB;

    const availableVram = gpuSpec.vram;

    // 5. Speed Estimation (TPS)
    // Formula: (Bandwidth / Model Size) * Efficiency
    // Penalties: MoE Expert Switching (0.8x)
    let baseEstimatedTps = (gpuSpec.bandwidth / Math.max(totalModelSizeGB, 1)) * 0.55;
    if (isMoE) baseEstimatedTps *= 0.8;

    // If offloading happens, apply 0.5x penalty as experts move between RAM and VRAM
    const isOffloading = availableVram < idealRequiredVram && availableVram >= offloadRequiredVram;
    const finalTps = isOffloading ? baseEstimatedTps * 0.5 : baseEstimatedTps;

    // 6. Multi-Dimensional Scoring (0-100)
    // Fit Score: Sweet spot 50-80%
    const usageRatio = (isOffloading ? offloadRequiredVram : idealRequiredVram) / availableVram;
    let fitScore = 0;
    if (usageRatio <= 0.8) fitScore = 100;
    else if (usageRatio <= 0.95) fitScore = 70;
    else if (usageRatio <= 1.0) fitScore = 40;
    else fitScore = 10;

    // Quality Score: Based on BPW
    const qualityScore = (bytesPerParam / 2.0) * 100; // float16 = 100%

    // Speed Score: TPS factor (assuming 50 TPS is amazing)
    const speedScore = Math.min((finalTps / 50) * 100, 100);

    // Context Score: Model capability
    const contextScore = Math.min((contextLength / 32768) * 100, 100);

    const score = (fitScore * 0.4) + (qualityScore * 0.2) + (speedScore * 0.3) + (contextScore * 0.1);

    // 7. Determine Fit Level
    let fitLevel: FitLevel = "TooTight";
    let isCompatible = false;

    if (availableVram >= idealRequiredVram) {
        fitLevel = "Perfect";
        isCompatible = true;
    } else if (availableVram >= offloadRequiredVram) {
        fitLevel = "Good";
        isCompatible = true;
    } else if (availableVram >= offloadRequiredVram * 0.8) {
        fitLevel = "Marginal";
        isCompatible = true;
    } else {
        fitLevel = "TooTight";
        isCompatible = false;
    }

    let reason = "";
    if (fitLevel === "Perfect") reason = `Fits entirely in VRAM for maximum speed.`;
    else if (fitLevel === "Good") reason = `Fits using Expert Offloading. Expect ${isMoE ? "MoE" : "CPU"} latency.`;
    else if (fitLevel === "Marginal") reason = `Extremely tight fit. May require reduced context or OOM risk.`;
    else reason = `Model size (${totalModelSizeGB.toFixed(1)}GB) exceeds available VRAM (${availableVram}GB).`;

    return {
        fitLevel,
        requiredVram: idealRequiredVram,
        availableVram,
        isCompatible,
        score: Math.round(score),
        estimatedTps: finalTps,
        reason,
        details: { qualityScore, speedScore, fitScore, contextScore }
    };
}
