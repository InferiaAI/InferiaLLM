/**
 * Ollama Registry Service
 * Fetches models from Ollama's public registry API
 */

const OLLAMA_API_BASE = "https://ollama.com/api";

export interface OllamaModel {
  name: string;
  model: string;
  modified_at: string;
  size: number;
  digest: string;
  details: {
    parent_model: string;
    format: string;
    family: string;
    families: string[] | null;
    parameter_size: string;
    quantization_level: string;
  };
}

/**
 * Fetch popular models from Ollama registry
 */
export async function getOllamaModels(): Promise<OllamaModel[]> {
  const response = await fetch(`${OLLAMA_API_BASE}/tags`);
  if (!response.ok) {
    throw new Error(`Failed to fetch Ollama models: ${response.statusText}`);
  }
  const data = await response.json();
  return data.models || [];
}

/**
 * Search Ollama models (client-side filter on the registry list)
 */
export async function searchOllamaModels(query: string): Promise<OllamaModel[]> {
  const models = await getOllamaModels();
  if (!query) return models;
  const q = query.toLowerCase();
  return models.filter(m => m.name.toLowerCase().includes(q));
}

/**
 * Format Ollama model size for display
 */
export function formatModelSize(sizeBytes: number): string {
  const gb = sizeBytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = sizeBytes / (1024 * 1024);
  return `${mb.toFixed(0)} MB`;
}

/**
 * Popular Ollama models as a hardcoded fallback
 */
export const POPULAR_OLLAMA_MODELS: OllamaModel[] = [
  { name: "llama3.1", model: "llama3.1", modified_at: "", size: 4700000000, digest: "", details: { parent_model: "", format: "", family: "llama", families: null, parameter_size: "8B", quantization_level: "Q4_0" } },
  { name: "llama3.1:70b", model: "llama3.1:70b", modified_at: "", size: 40000000000, digest: "", details: { parent_model: "", format: "", family: "llama", families: null, parameter_size: "70B", quantization_level: "Q4_0" } },
  { name: "qwen2", model: "qwen2", modified_at: "", size: 4400000000, digest: "", details: { parent_model: "", format: "", family: "qwen2", families: null, parameter_size: "7B", quantization_level: "Q4_0" } },
  { name: "mistral", model: "mistral", modified_at: "", size: 4100000000, digest: "", details: { parent_model: "", format: "", family: "mistral", families: null, parameter_size: "7B", quantization_level: "Q4_0" } },
  { name: "phi3", model: "phi3", modified_at: "", size: 2200000000, digest: "", details: { parent_model: "", format: "", family: "phi3", families: null, parameter_size: "3.8B", quantization_level: "Q4_0" } },
  { name: "gemma2", model: "gemma2", modified_at: "", size: 5400000000, digest: "", details: { parent_model: "", format: "", family: "gemma2", families: null, parameter_size: "9B", quantization_level: "Q4_0" } },
  { name: "codellama", model: "codellama", modified_at: "", size: 3800000000, digest: "", details: { parent_model: "", format: "", family: "llama", families: null, parameter_size: "7B", quantization_level: "Q4_0" } },
  { name: "deepseek-coder-v2", model: "deepseek-coder-v2", modified_at: "", size: 8900000000, digest: "", details: { parent_model: "", format: "", family: "deepseek", families: null, parameter_size: "16B", quantization_level: "Q4_0" } },
];
