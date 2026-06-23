import { describe, it, expect } from "vitest";
import { buildDiffusionSpec } from "../newDeploymentSpec";

describe("buildDiffusionSpec", () => {
  it("builds a diffusion spec on port 8000 with config block and no raw token env", () => {
    const spec = JSON.parse(buildDiffusionSpec({
      modelId: "stabilityai/sdxl-turbo",
      modelType: "image_generation",
      trustRemoteCode: true,
      modelOffload: false,
      groupOffload: false,
    }));
    expect(spec.port).toBe(8000);
    expect(spec.expose[0].port).toBe(8000);
    expect(spec.engine).toBe("inferia-diffusion");
    expect(spec.image).toBe("docker.io/inferiaai/inferiadiffusion:latest");
    expect(spec.config.model_type).toBe("image_generation");
    expect(spec.config.trust_remote_code).toBe(true);
    expect(spec.config.model_offload).toBeUndefined();
    expect(spec.config.group_offload).toBeUndefined();
    expect(spec.env?.HF_TOKEN).toBeUndefined();
  });

  it("defaults the model id and sets video model_type", () => {
    const spec = JSON.parse(buildDiffusionSpec({ modelId: "", modelType: "video_generation" }));
    expect(spec.model_id).toBe("segmind/tiny-sd");
    expect(spec.config.model_type).toBe("video_generation");
  });

  it("includes offload flags only when enabled", () => {
    const spec = JSON.parse(buildDiffusionSpec({
      modelId: "stabilityai/sdxl-turbo",
      modelType: "image_generation",
      trustRemoteCode: false,
      modelOffload: true,
      groupOffload: true,
    }));
    expect(spec.config.model_offload).toBe(true);
    expect(spec.config.group_offload).toBe(true);
    // trust_remote_code off → omitted
    expect(spec.config.trust_remote_code).toBeUndefined();
  });
});
