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
  },
  video_generation: {
    id: "video_generation",
    label: "Video Generation",
    description: "Text-to-video and image-to-video models",
    icon: "Video",
    pipeline_tags: ["text-to-video", "image-to-video"],
    default_backend: "diffusers-video",
  },
  multimodal: {
    id: "multimodal",
    label: "Multimodal (Vision)",
    description: "Vision-language models that understand images and text",
    icon: "Eye",
    pipeline_tags: ["visual-question-answering", "image-text-to-text"],
    default_backend: "vllm",
  },
  audio_generation: {
    id: "audio_generation",
    label: "Audio",
    description: "Speech recognition and text-to-speech models",
    icon: "Volume2",
    pipeline_tags: ["automatic-speech-recognition", "text-to-speech"],
    default_backend: "whisper",
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
 * Get model native config.json
 */
export async function getModelConfig(modelId: string): Promise<any> {
  try {
    const res = await fetch(`https://huggingface.co/${modelId}/raw/main/config.json`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
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
 * Recommended image generation models
 */
export const IMAGE_GENERATION_MODELS = [
  {
    id: "stabilityai/stable-diffusion-xl-base-1.0",
    name: "SDXL Base 1.0",
    description: "Stable Diffusion XL, high quality 1024x1024 images",
    downloads: 5000000,
  },
  {
    id: "stabilityai/stable-diffusion-3-medium-diffusers",
    name: "SD 3 Medium",
    description: "Stable Diffusion 3 medium model",
    downloads: 2000000,
  },
  {
    id: "runwayml/stable-diffusion-v1-5",
    name: "SD 1.5",
    description: "Classic Stable Diffusion v1.5, widely supported",
    downloads: 10000000,
  },
  {
    id: "black-forest-labs/FLUX.1-schnell",
    name: "FLUX.1 Schnell",
    description: "Fast high-quality image generation",
    downloads: 3000000,
  },
];

/**
 * Recommended video generation models
 */
export const VIDEO_GENERATION_MODELS = [
  {
    id: "stabilityai/stable-video-diffusion-img2vid-xt",
    name: "SVD img2vid-xt",
    description: "Image-to-video, 25 frames at 576x1024",
    downloads: 500000,
  },
  {
    id: "THUDM/CogVideoX-5b",
    name: "CogVideoX 5B",
    description: "Text-to-video, 6 seconds at 720p",
    downloads: 200000,
  },
];

/**
 * Recommended audio models
 */
export const AUDIO_MODELS = [
  {
    id: "openai/whisper-large-v3",
    name: "Whisper Large v3",
    description: "Best accuracy speech recognition, multilingual",
    downloads: 3000000,
  },
  {
    id: "openai/whisper-large-v3-turbo",
    name: "Whisper Large v3 Turbo",
    description: "Fast speech recognition with near-best accuracy",
    downloads: 1500000,
  },
  {
    id: "suno/bark",
    name: "Bark",
    description: "Text-to-speech with voice presets and sound effects",
    downloads: 1000000,
  },
];

/**
 * Recommended multimodal models
 */
export const MULTIMODAL_MODELS = [
  {
    id: "llava-hf/llava-v1.6-mistral-7b-hf",
    name: "LLaVA v1.6 7B",
    description: "Vision-language model, image understanding",
    downloads: 500000,
  },
  {
    id: "Qwen/Qwen2-VL-7B-Instruct",
    name: "Qwen2-VL 7B",
    description: "Qwen vision-language model, strong OCR",
    downloads: 800000,
  },
  {
    id: "meta-llama/Llama-3.2-11B-Vision-Instruct",
    name: "Llama 3.2 Vision 11B",
    description: "Meta's vision-language model",
    downloads: 600000,
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
