/**
 * Hugging Face Hub API Service
 * Provides methods to browse and search models from Hugging Face
 */

const HF_API_BASE = "https://huggingface.co/api";

export interface HFModel {
  id: string;
  modelId: string;
  author: string;
  lastModified: string;
  tags: string[];
  pipeline_tag: string | null;
  downloads: number;
  likes: number;
  library_name: string | null;
  config?: {
    model_type?: string;
    architectures?: string[];
  };
}

export interface ModelSearchFilters {
  pipeline_tag?: string;
  library?: string;
  sort?: "downloads" | "likes" | "lastModified";
  limit?: number;
  search?: string;
}

/**
 * Model type definitions with metadata
 */
export const MODEL_TYPES = {
  inference: {
    id: "inference",
    label: "Text Generation (LLM)",
    description: "Large Language Models for chat and completion",
    icon: "MessageSquare",
    pipeline_tags: ["text-generation", "text2text-generation", "conversational"],
    default_backend: "vllm",
  },
  embedding: {
    id: "embedding",
    label: "Embeddings",
    description: "Text embedding models for vectorization and semantic search",
    icon: "Database",
    pipeline_tags: ["feature-extraction", "sentence-similarity"],
    default_backend: "infinity",
  },
  image_generation: {
    id: "image_generation",
    label: "Image Generation",
    description: "Stable Diffusion and image generation models",
    icon: "Image",
    pipeline_tags: ["text-to-image", "image-to-image", "inpainting"],
    default_backend: "diffusers",
    coming_soon: true,
  },
  multimodal: {
    id: "multimodal",
    label: "Multimodal (Vision)",
    description: "Vision-language models that understand images and text",
    icon: "Eye",
    pipeline_tags: ["visual-question-answering", "image-text-to-text"],
    default_backend: "vllm",
    coming_soon: true,
  },
  audio: {
    id: "audio",
    label: "Audio",
    description: "Speech recognition and text-to-speech models",
    icon: "Volume2",
    pipeline_tags: ["automatic-speech-recognition", "text-to-speech"],
    default_backend: "whisper",
    coming_soon: true,
  },
} as const;

export type ModelTypeKey = keyof typeof MODEL_TYPES;

/**
 * Infer model type from HF pipeline tag
 */
export function inferModelType(pipelineTag: string | null): ModelTypeKey {
  if (!pipelineTag) return "inference";
  
  for (const [typeKey, typeInfo] of Object.entries(MODEL_TYPES)) {
    if (typeInfo.pipeline_tags.includes(pipelineTag)) {
      return typeKey as ModelTypeKey;
    }
  }
  
  return "inference";
}

/**
 * Search models on Hugging Face Hub
 */
export async function searchHFModels(
  filters: ModelSearchFilters = {}
): Promise<HFModel[]> {
  const params = new URLSearchParams();
  
  if (filters.search) {
    params.append("search", filters.search);
  }
  
  if (filters.pipeline_tag) {
    params.append("filter", filters.pipeline_tag);
  }
  
  if (filters.library) {
    params.append("library", filters.library);
  }
  
  if (filters.sort) {
    params.append("sort", filters.sort);
  }
  
  params.append("limit", String(filters.limit || 50));
  params.append("full", "true");
  params.append("config", "true");
  
  const response = await fetch(`${HF_API_BASE}/models?${params.toString()}`);
  
  if (!response.ok) {
    throw new Error(`Failed to fetch models: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Get trending/popular models
 */
export async function getPopularModels(
  modelType: ModelTypeKey = "inference",
  limit: number = 20
): Promise<HFModel[]> {
  const typeInfo = MODEL_TYPES[modelType];
  const pipelineTag = typeInfo.pipeline_tags[0];
  
  return searchHFModels({
    pipeline_tag: pipelineTag,
    sort: "downloads",
    limit,
  });
}

/**
 * Get model details by ID
 */
export async function getModelDetails(modelId: string): Promise<HFModel> {
  const response = await fetch(
    `${HF_API_BASE}/models/${modelId}?full=true&config=true`
  );
  
  if (!response.ok) {
    throw new Error(`Failed to fetch model details: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Get embedding-specific recommended models
 */
export const EMBEDDING_MODELS = [
  {
    id: "sentence-transformers/all-MiniLM-L6-v2",
    name: "all-MiniLM-L6-v2",
    description: "Lightweight embedding model, 384 dimensions",
    downloads: 5000000,
    dimensions: 384,
    max_sequence_length: 256,
  },
  {
    id: "sentence-transformers/all-mpnet-base-v2",
    name: "all-mpnet-base-v2",
    description: "High quality embeddings, 768 dimensions",
    downloads: 3000000,
    dimensions: 768,
    max_sequence_length: 384,
  },
  {
    id: "BAAI/bge-large-en-v1.5",
    name: "bge-large-en-v1.5",
    description: "Best general embeddings, 1024 dimensions",
    downloads: 1000000,
    dimensions: 1024,
    max_sequence_length: 512,
  },
  {
    id: "sentence-transformers/multi-qa-MiniLM-L6-cos-v1",
    name: "multi-qa-MiniLM-L6-cos-v1",
    description: "Optimized for QA and semantic search",
    downloads: 500000,
    dimensions: 384,
    max_sequence_length: 512,
  },
  {
    id: "thenlper/gte-large",
    name: "gte-large",
    description: "General text embeddings by Alibaba",
    downloads: 400000,
    dimensions: 1024,
    max_sequence_length: 512,
  },
];

/**
 * Format model download count
 */
export function formatDownloads(count: number): string {
  if (count >= 1000000) {
    return `${(count / 1000000).toFixed(1)}M`;
  }
  if (count >= 1000) {
    return `${(count / 1000).toFixed(1)}K`;
  }
  return String(count);
}
