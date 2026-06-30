/**
 * Unit tests for the `requiresAmi` pure helper exported from NewDeployment.tsx.
 *
 * Approach: extracted-helper unit tests.
 * The helper encapsulates the AMI-gating logic used at three sites in the file
 * (validation, payload construction, AMI-dropdown render).  Testing it directly
 * avoids wiring up the full 1600-line wizard (reducer, queries, router, auth
 * context) while giving complete coverage of every gating branch.
 */

import { describe, it, expect } from "vitest";
import { requiresAmi, requiresAwsPool } from "../NewDeployment";

// A minimal pool shape — only `provider` is needed by the helper.
const awsPool = { provider: "aws" } as const;
const nosanaPool = { provider: "nosana" } as const;
const akashPool = { provider: "akash" } as const;
const workerPool = { provider: "worker" } as const;

describe("requiresAmi", () => {
  // ── AWS + vLLM ────────────────────────────────────────────────────────────
  it("returns true for aws pool with vllm engine", () => {
    expect(requiresAmi("vllm", awsPool)).toBe(true);
  });

  // ── Non-AWS providers + vLLM ──────────────────────────────────────────────
  it("returns false for nosana pool with vllm engine", () => {
    expect(requiresAmi("vllm", nosanaPool)).toBe(false);
  });

  it("returns false for akash pool with vllm engine", () => {
    expect(requiresAmi("vllm", akashPool)).toBe(false);
  });

  it("returns false for worker pool with vllm engine", () => {
    expect(requiresAmi("vllm", workerPool)).toBe(false);
  });

  // ── Non-vLLM engines on AWS ───────────────────────────────────────────────
  it("returns false for aws pool with ollama engine", () => {
    expect(requiresAmi("ollama", awsPool)).toBe(false);
  });

  it("returns false for aws pool with infinity engine", () => {
    expect(requiresAmi("infinity", awsPool)).toBe(false);
  });

  it("returns false for aws pool with tei engine", () => {
    expect(requiresAmi("tei", awsPool)).toBe(false);
  });

  it("returns false for aws pool with pytorch engine", () => {
    expect(requiresAmi("pytorch", awsPool)).toBe(false);
  });

  // ── Null / undefined pool (no pool selected yet) ──────────────────────────
  it("returns false when pool is null regardless of engine", () => {
    expect(requiresAmi("vllm", null)).toBe(false);
  });

  it("returns false when pool is undefined regardless of engine", () => {
    expect(requiresAmi("vllm", undefined)).toBe(false);
  });

  // ── Pool with no provider field ───────────────────────────────────────────
  it("returns false when pool has no provider property", () => {
    expect(requiresAmi("vllm", {})).toBe(false);
  });

  // ── Empty / blank engine string ───────────────────────────────────────────
  it("returns false for empty engine string on aws pool", () => {
    expect(requiresAmi("", awsPool)).toBe(false);
  });

  // ── Non-vLLM engines on non-AWS providers ─────────────────────────────────
  it("returns false for nosana pool with ollama engine", () => {
    expect(requiresAmi("ollama", nosanaPool)).toBe(false);
  });

  // vllm-omni / inferia-diffusion do NOT use an engine AMI (deploy like
  // sglang/ollama), so requiresAmi is always false even on AWS.
  it("returns false for aws pool with vllm-omni engine", () => {
    expect(requiresAmi("vllm-omni", awsPool)).toBe(false);
  });

  it("returns false for aws pool with inferia-diffusion engine", () => {
    expect(requiresAmi("inferia-diffusion", awsPool)).toBe(false);
  });
});

describe("requiresAwsPool", () => {
  it("returns true for vllm-omni", () => {
    expect(requiresAwsPool("vllm-omni")).toBe(true);
  });

  it("returns false for inferia-diffusion (now deployable on any provider)", () => {
    expect(requiresAwsPool("inferia-diffusion")).toBe(false);
  });

  it("returns false for vllm / sglang / ollama / tei / infinity / pytorch", () => {
    for (const e of ["vllm", "sglang", "ollama", "tei", "infinity", "pytorch"]) {
      expect(requiresAwsPool(e)).toBe(false);
    }
  });

  it("returns false for an empty engine string", () => {
    expect(requiresAwsPool("")).toBe(false);
  });
});
