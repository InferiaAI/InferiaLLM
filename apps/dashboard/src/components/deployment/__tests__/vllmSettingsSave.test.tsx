import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import DeploymentConfig from "../DeploymentConfig";

const patch = vi.fn().mockResolvedValue({ data: {} });
vi.mock("@/lib/api", () => ({
  computeApi: { patch: (...args: unknown[]) => patch(...args) },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const deployment = (configuration: Record<string, unknown>) => ({
  id: "dep-1",
  engine: "vllm",
  replicas: 1,
  inference_model: "org/model",
  configuration,
}) as never;

async function saved(): Promise<Record<string, any>> {
  fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
  await waitFor(() => expect(patch).toHaveBeenCalled());
  return patch.mock.calls[0][1].configuration;
}

const openAdvanced = () =>
  fireEvent.click(screen.getByRole("button", { name: /advanced configuration/i }));

describe("the edit page's vLLM settings", () => {
  beforeEach(() => patch.mockClear());

  it("saves max model length and GPU util for a deployment with no cmd", async () => {
    // The dashboard creates vLLM deployments without one, and these two used to
    // be written only into cmd, so they were lost.
    render(<DeploymentConfig deployment={deployment({})} />);
    fireEvent.change(screen.getByLabelText(/max model length/i), { target: { value: "8192" } });
    fireEvent.change(screen.getByLabelText(/gpu util/i), { target: { value: "0.7" } });

    const config = await saved();
    expect(config.max_model_len).toBe(8192);
    expect(config.gpu_memory_utilization).toBe(0.7);
    expect(config.cmd).toBeUndefined();
  });

  it("shows saved values again when reopened", () => {
    render(<DeploymentConfig deployment={deployment({ max_model_len: 8192, gpu_memory_utilization: 0.7 })} />);
    expect(screen.getByLabelText(/max model length/i)).toHaveValue("8192");
    expect(screen.getByLabelText(/gpu util/i)).toHaveValue("0.7");
  });

  it("reads the older gpu_util name, and saves it under the engine's name", async () => {
    render(<DeploymentConfig deployment={deployment({ gpu_util: 0.6 })} />);
    expect(screen.getByLabelText(/gpu util/i)).toHaveValue("0.6");

    const config = await saved();
    expect(config.gpu_memory_utilization).toBe(0.6);
    expect(config.gpu_util).toBeUndefined();
  });

  it("adds nothing when a deployment with no settings is saved untouched", async () => {
    // Otherwise opening and saving would pin eager mode, remote code and a
    // sequence limit on a deployment that never asked for them.
    render(<DeploymentConfig deployment={deployment({})} />);

    const config = await saved();
    for (const key of ["max_model_len", "gpu_memory_utilization", "dtype", "quantization",
      "enforce_eager", "max_num_seqs", "kv_cache_dtype", "trust_remote_code"]) {
      expect(config, key).not.toHaveProperty(key);
    }
  });

  it("leaves remote code off unless it is ticked", () => {
    render(<DeploymentConfig deployment={deployment({})} />);
    openAdvanced();
    expect(screen.getByLabelText(/trust remote code/i)).not.toBeChecked();
  });

  it("saves the advanced settings that are set", async () => {
    render(<DeploymentConfig deployment={deployment({})} />);
    openAdvanced();
    fireEvent.change(screen.getByLabelText(/data type/i), { target: { value: "bfloat16" } });
    fireEvent.change(screen.getByLabelText(/kv cache dtype/i), { target: { value: "fp8" } });
    fireEvent.change(screen.getByLabelText(/max num sequences/i), { target: { value: "64" } });
    fireEvent.change(screen.getByLabelText(/quantization/i), { target: { value: "fp8" } });
    fireEvent.click(screen.getByLabelText(/enforce eager mode/i));
    fireEvent.click(screen.getByLabelText(/trust remote code/i));

    expect(await saved()).toMatchObject({
      dtype: "bfloat16", kv_cache_dtype: "fp8", max_num_seqs: 64, quantization: "fp8",
      enforce_eager: true, trust_remote_code: true,
    });
  });

  it("clearing a field removes the setting", async () => {
    render(<DeploymentConfig deployment={deployment({ max_model_len: 8192, enforce_eager: true })} />);
    fireEvent.change(screen.getByLabelText(/max model length/i), { target: { value: "" } });
    openAdvanced();
    fireEvent.click(screen.getByLabelText(/enforce eager mode/i));

    const config = await saved();
    expect(config).not.toHaveProperty("max_model_len");
    expect(config).not.toHaveProperty("enforce_eager");
  });

  it("an edit beats the nested block, which the backend prefers", async () => {
    render(<DeploymentConfig deployment={deployment({ config: { max_model_len: 4096, tensor_parallel_size: 2 } })} />);
    expect(screen.getByLabelText(/max model length/i)).toHaveValue("4096");
    fireEvent.change(screen.getByLabelText(/max model length/i), { target: { value: "8192" } });

    const config = await saved();
    expect(config.max_model_len).toBe(8192);
    expect(config.config).toEqual({ tensor_parallel_size: 2 });
  });
});

describe("the quantization choices", () => {
  it("offers only methods vLLM accepts", () => {
    // From vllm/model_executor/layers/quantization at v0.22.1; it refuses others at startup.
    const accepted = ["", "awq", "awq_marlin", "gptq", "gptq_marlin", "fp8", "bitsandbytes", "gguf",
      "compressed-tensors", "experts_int8"];
    render(<DeploymentConfig deployment={deployment({})} />);
    openAdvanced();
    const select = screen.getByLabelText(/quantization/i) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(accepted);
  });
});
