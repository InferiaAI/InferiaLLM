import { describe, it, expect, vi, afterEach } from "vitest";

import {
  resolvePoolGpuResources,
  calculatePoolCompatibility,
  calculatePoolCompatibilityWithFit,
} from "../modelPlanner";

const HF_CONFIG_14B = {
  max_position_embeddings: 32768,
  hidden_size: 5120,
  num_hidden_layers: 48,
  num_attention_heads: 40,
  num_key_value_heads: 8,
};

function pool(over: Record<string, unknown> = {}) {
  return { gpu_count: 1, allowed_gpu_types: ["any"], ...over };
}

describe("resolvePoolGpuResources", () => {
  it("reports the size as unknown for an any-GPU pool", () => {
    const r = resolvePoolGpuResources(pool());
    expect(r.vramKnown).toBe(false);
    expect(r.singleGpuVram).toBe(0);
  });

  it("takes the real size from gpu_specs when the backend supplies it", () => {
    const r = resolvePoolGpuResources(
      pool({ gpu_specs: [{ gpu_type: "A10G", vram: 24 }] }),
    );
    expect(r.vramKnown).toBe(true);
    expect(r.singleGpuVram).toBe(24);
  });

  it("still resolves a pool that names a GPU it knows", () => {
    const r = resolvePoolGpuResources(pool({ allowed_gpu_types: ["A5000"] }));
    expect(r.vramKnown).toBe(true);
    expect(r.singleGpuVram).toBe(24);
  });
});

describe("calculatePoolCompatibility", () => {
  it("says the GPU is unknown rather than inventing a budget", () => {
    const res = calculatePoolCompatibility("Qwen/Qwen2.5-14B", pool(), HF_CONFIG_14B, "awq", "auto");

    expect(res?.fitLevel).toBe("Unknown");
    expect(res?.reason).toMatch(/does not say which GPU/i);
    expect(res?.reason).not.toMatch(/safe single-GPU budget/i);
  });

  it("does not call a model incompatible on a GPU it cannot identify", () => {
    const res = calculatePoolCompatibility("Qwen/Qwen2.5-14B", pool(), HF_CONFIG_14B, "awq", "auto");
    expect(res?.isCompatible).toBe(true);
  });

  it("uses a single GPU's real size instead of discarding it", () => {
    const res = calculatePoolCompatibility(
      "Qwen/Qwen2.5-14B",
      pool({ gpu_specs: [{ gpu_type: "A10G", vram: 24 }] }),
      HF_CONFIG_14B,
      "awq",
      "auto",
    );

    expect(res?.fitLevel).not.toBe("Unknown");
    expect(res?.availableVram).toBeGreaterThan(20);
  });

  it("still judges a pool that names a known GPU", () => {
    const res = calculatePoolCompatibility(
      "Qwen/Qwen2.5-14B",
      pool({ allowed_gpu_types: ["A5000"] }),
      HF_CONFIG_14B,
      "awq",
      "auto",
    );
    expect(res?.fitLevel).not.toBe("Unknown");
    expect(res?.availableVram).toBeGreaterThan(20);
  });
});

describe("AWS pools", () => {
  const awsPool = pool({
    allowed_gpu_types: ["g5.xlarge"],
    gpu_specs: [{ gpu_type: "NVIDIA A10G", vram: 24 }],
  });

  it("uses the card the backend resolved from the instance type", () => {
    const r = resolvePoolGpuResources(awsPool);
    expect(r.vramKnown).toBe(true);
    expect(r.singleGpuVram).toBe(24);
    expect(r.gpuSpecKey).toBe("A10G");
  });

  it("gives a real verdict rather than Unknown", () => {
    const res = calculatePoolCompatibility(
      "Qwen/Qwen2.5-14B", awsPool, HF_CONFIG_14B, "awq", "auto",
    );
    expect(res?.fitLevel).not.toBe("Unknown");
    expect(res?.availableVram).toBeGreaterThan(20);
  });

  it("is Unknown when the instance type resolves to nothing", () => {
    const res = calculatePoolCompatibility(
      "Qwen/Qwen2.5-14B",
      pool({ allowed_gpu_types: ["g5.xlarge"] }),
      HF_CONFIG_14B, "awq", "auto",
    );
    expect(res?.fitLevel).toBe("Unknown");
  });
});

describe("calculatePoolCompatibilityWithFit", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not ask llmfit about a GPU whose size is unknown", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes("/health")) {
        return { ok: true, json: async () => ({}) } as unknown as Response;
      }
      throw new Error(`llmfit must not be queried for an unknown GPU: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const res = await calculatePoolCompatibilityWithFit(
      "Qwen/Qwen2.5-14B", pool(), HF_CONFIG_14B, "awq", "auto",
    );

    expect(res?.fitLevel).toBe("Unknown");
    const queried = fetchMock.mock.calls.map(c => String(c[0]));
    expect(queried.some(u => !u.includes("/health"))).toBe(false);
  });
});
