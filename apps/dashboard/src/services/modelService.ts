import { computeApi } from "@/lib/api";

export interface CachedModel {
  id: string;
  source: string;
  model_id: string;
  revision: string;
  status: string;
  bytes_total: number;
  bytes_done: number;
  error?: string | null;
  engine_hint?: string | null;
}

export async function listModels(): Promise<CachedModel[]> {
  const r = await computeApi.get<{ models: CachedModel[] }>("/models");
  return r.data.models || [];
}

export async function addModel(body: {
  source: string;
  model_id: string;
  revision?: string;
  engine?: string;
}) {
  const r = await computeApi.post("/models", body);
  return r.data;
}

export async function deleteModel(id: string): Promise<void> {
  await computeApi.delete(`/models/${id}`);
}
