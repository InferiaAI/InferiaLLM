import { describe, it, expect } from "vitest";

import { buildVllmJobSpec, buildVllmSettings } from "../newDeploymentSpec";

describe("buildVllmSettings", () => {
  it("sends what the form says, under the engine's names", () => {
    expect(buildVllmSettings({
      maxModelLen: "8192", gpuUtil: "0.8", dtype: "bfloat16", quantization: "fp8", enforceEager: true,
    })).toEqual({
      max_model_len: 8192, gpu_memory_utilization: 0.8, dtype: "bfloat16", quantization: "fp8", enforce_eager: true,
    });
  });

  it("sends nothing for an untouched form, so the worker picks for the GPU", () => {
    expect(buildVllmSettings({
      maxModelLen: "", gpuUtil: "", dtype: "auto", quantization: "", enforceEager: false,
    })).toEqual({});
  });

  it.each([
    ["maxModelLen", "0"], ["maxModelLen", "-5"], ["maxModelLen", "lots"],
    ["gpuUtil", "0"], ["gpuUtil", "1.5"], ["gpuUtil", "half"],
  ])("leaves out an impossible %s of %s", (field, value) => {
    expect(buildVllmSettings({ [field]: value })).toEqual({});
  });

  it("accepts a GPU util of exactly 1", () => {
    expect(buildVllmSettings({ gpuUtil: "1" })).toEqual({ gpu_memory_utilization: 1 });
  });
});

describe("buildVllmJobSpec", () => {
  const form = { modelId: "org/model", prefillReplicas: "0", decodeReplicas: "0" };

  it("carries the form's settings to the deployment", () => {
    const spec = JSON.parse(buildVllmJobSpec({
      ...form, engine: "vllm", maxModelLen: "8192", gpuUtil: "0.8", dtype: "bfloat16",
      quantization: "fp8", enforceEager: true,
    }));
    expect(spec).toMatchObject({
      model_id: "org/model", engine: "vllm", max_model_len: 8192, gpu_memory_utilization: 0.8,
      dtype: "bfloat16", quantization: "fp8", enforce_eager: true,
    });
  });

  it("an untouched form adds no settings", () => {
    const spec = JSON.parse(buildVllmJobSpec({ ...form, engine: "vllm", dtype: "auto" }));
    expect(Object.keys(spec).sort()).toEqual(["engine", "expose", "gpu", "model_id"]);
  });

  it("does not send vLLM settings for SGLang", () => {
    const spec = JSON.parse(buildVllmJobSpec({ ...form, engine: "sglang", maxModelLen: "8192" }));
    expect(spec).not.toHaveProperty("max_model_len");
  });

  it("keeps the health check and the prefill-decode split", () => {
    const spec = JSON.parse(buildVllmJobSpec({
      ...form, engine: "vllm", prefillReplicas: "1", decodeReplicas: "2",
      prefillGpuIndices: "0,1", decodeGpuIndices: "2",
    }));
    expect(spec.expose[0].health_checks[0].path).toBe("/v1/chat/completions");
    expect(spec.prefill_replicas).toBe(1);
    expect(spec.decode_replicas).toBe(2);
    expect(spec.prefill_gpu_indices).toEqual([0, 1]);
    expect(spec.decode_gpu_indices).toEqual([2]);
  });
});
