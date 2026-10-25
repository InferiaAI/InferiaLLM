import { describe, expect, it } from "vitest";
import type { Deployment } from "@/hooks/useReadyDeployments";
import { MODEL_CATALOG, deployRequests, nodeReadiness, runBlocker } from "../readiness";
import type { Workflow } from "../types";

function dep(modelName: string, model_type: string): Deployment {
  return { id: modelName, name: modelName, modelName, model_type, endpointUrl: "", status: "READY" };
}

const stt = { kind: "speechToText" as const, config: {} };
const llm = { kind: "llm" as const, config: {} };
const qwenCatalog = MODEL_CATALOG.llm!;

describe("nodeReadiness", () => {
  it("treats nodes without a model field, or with a model set, as ready", () => {
    expect(nodeReadiness({ kind: "textInput", config: {} }, [], undefined)).toEqual({ state: "ready" });
    expect(nodeReadiness({ kind: "llm", config: { model: "bank-llm" } }, [], undefined)).toEqual({ state: "ready" });
  });

  it("asks to select a model when a matching deployment exists", () => {
    expect(nodeReadiness(stt, [dep("whisper-stt", "audio_generation")])).toEqual({ state: "selectModel" });
    expect(nodeReadiness({ kind: "llm", config: {} }, [dep("bank-llm", "inference")])).toEqual({ state: "selectModel" });
  });

  it("needs deployment when only non-matching deployments exist", () => {
    expect(nodeReadiness(llm, [dep("whisper-stt", "audio_generation")])).toEqual({
      state: "needsDeployment",
      recommended: qwenCatalog,
    });
  });

  it("asks for an external deployment for speech, which no pool engine can run", () => {
    expect(nodeReadiness(stt, [dep("kokoro-tts", "audio_generation"), dep("bank-llm", "inference")])).toEqual({
      state: "needsExternal",
    });
    expect(nodeReadiness({ kind: "textToSpeech", config: {} }, [])).toEqual({ state: "needsExternal" });
  });

  it("follows the pending deploy through deploying, waiting, failed and deployed", () => {
    expect(nodeReadiness(llm, [], { status: "deploying", deploymentName: "qwen2-5-1-5b" })).toEqual({
      state: "deploying",
      recommended: qwenCatalog,
    });
    expect(nodeReadiness(llm, [], { status: "waiting", reason: "no active compute pool" })).toEqual({
      state: "waiting",
      recommended: qwenCatalog,
      reason: "no active compute pool",
    });
    expect(nodeReadiness(llm, [], { status: "failed", reason: "image pull failed" })).toEqual({
      state: "failed",
      recommended: qwenCatalog,
      reason: "image pull failed",
    });
    expect(
      nodeReadiness(llm, [dep("qwen2-5-1-5b", "inference")], { status: "deploying", deploymentName: "qwen2-5-1-5b" }),
    ).toEqual({ state: "deployed", model: "qwen2-5-1-5b" });
  });
});

describe("MODEL_CATALOG", () => {
  it("only recommends engines that worker pools can run", () => {
    const poolEngines = ["vllm", "vllm-omni", "ollama", "infinity", "triton", "inferia-diffusion"];
    for (const entry of Object.values(MODEL_CATALOG)) {
      expect(poolEngines).toContain(entry!.engine);
      expect(entry!.job.engine).toBe(entry!.engine);
    }
  });
});

describe("runBlocker", () => {
  const wf = (configs: Array<Record<string, string>>): Workflow => ({
    nodes: [
      { id: "a", kind: "audioInput", position: { x: 0, y: 0 }, config: {} },
      { id: "s", kind: "speechToText", position: { x: 0, y: 0 }, config: configs[0] },
      { id: "l", kind: "llm", position: { x: 0, y: 0 }, config: configs[1] },
    ],
    edges: [],
  });

  it("is null when every node is ready", () => {
    expect(runBlocker(wf([{ model: "w" }, { model: "l" }]), [], {})).toBeNull();
  });

  it("names the first blocked node and its reason", () => {
    expect(runBlocker(wf([{}, { model: "l" }]), [], {})).toBe("Speech to text: Needs an external deployment");
    expect(runBlocker(wf([{ model: "w" }, {}]), [], { l: { status: "waiting", reason: "no active compute pool" } })).toBe(
      "LLM: Waiting for compute: no active compute pool",
    );
    expect(runBlocker(wf([{ model: "w" }, {}]), [], { l: { status: "failed", reason: "image pull failed" } })).toBe(
      "LLM: Deploy failed: image pull failed",
    );
    expect(runBlocker(wf([{ model: "w" }, {}]), [], { l: { status: "deploying", deploymentName: "qwen2-5-1-5b" } })).toBe(
      "LLM: Deploying qwen2-5-1-5b",
    );
    expect(runBlocker(wf([{ model: "w" }, {}]), [dep("bank-llm", "inference")], {})).toBe("LLM: Select a model");
  });
});

describe("deployRequests", () => {
  it("builds the preflight and pool deploy payloads", () => {
    const req = deployRequests(MODEL_CATALOG.llm!, "pool-1", { orgId: "org-1", ownerId: "user-1" });
    expect(req.preflight).toEqual({
      model_id: "qwen2.5:1.5b",
      engine: "ollama",
      gpu_per_replica: 1,
      pool_id: "pool-1",
      model_type: "inference",
    });
    expect(req.deploy).toMatchObject({
      model_name: "qwen2-5-1-5b",
      model_version: "latest",
      replicas: 1,
      gpu_per_replica: 1,
      workload_type: "inference",
      pool_id: "pool-1",
      engine: "ollama",
      model_type: "inference",
      inference_model: "qwen2.5:1.5b",
      owner_id: "user-1",
      org_id: "org-1",
    });
  });
});
