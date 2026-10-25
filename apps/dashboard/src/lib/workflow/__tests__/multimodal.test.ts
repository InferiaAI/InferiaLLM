import { describe, expect, it, vi } from "vitest";
import { connectionError, runWorkflow } from "../engine";
import { intentError, planFromKeywords, plannerPrompt } from "../generate";
import { fitWithin, frameTimes } from "../media";
import { NODE_KINDS, imageGenerationBody, matchingDeployments, visionBody } from "../nodeKinds";
import { MODEL_CATALOG, nodeReadiness } from "../readiness";
import { TEMPLATES } from "../templates";
import type { NodeKind, Workflow } from "../types";

function wf(kinds: Record<string, NodeKind>): Workflow {
  return {
    nodes: Object.entries(kinds).map(([id, kind]) => ({ id, kind, position: { x: 0, y: 0 }, config: {} })),
    edges: [],
  };
}

const kinds = (w: Workflow) => w.nodes.map((n) => n.kind);
const dep = (modelName: string, model_type: string) =>
  ({ modelName, model_type }) as Parameters<typeof matchingDeployments>[1][number];

describe("image port edges", () => {
  const graph = wf({ img: "imageInput", vid: "videoInput", gen: "imageGeneration", vis: "vision", txt: "textInput", out: "imageOutput", tout: "textOutput", stt: "speechToText" });

  it("connects image producers to image consumers", () => {
    expect(connectionError(graph, "img", "vis")).toBeNull();
    expect(connectionError(graph, "vid", "vis")).toBeNull();
    expect(connectionError(graph, "gen", "out")).toBeNull();
    expect(connectionError(graph, "txt", "gen")).toBeNull();
    expect(connectionError(graph, "vis", "tout")).toBeNull();
  });

  it("rejects image to text, audio or wrong direction", () => {
    expect(connectionError(graph, "img", "tout")).toBe("Cannot connect image output to text input");
    expect(connectionError(graph, "vid", "stt")).toBe("Cannot connect image output to audio input");
    expect(connectionError(graph, "txt", "vis")).toBe("Cannot connect text output to image input");
    expect(connectionError(graph, "out", "vis")).toBe("Image output has no output");
  });
});

describe("payload builders", () => {
  it("builds the image generation body", () => {
    expect(imageGenerationBody("sd-turbo-image", "a red fox")).toEqual({
      model: "sd-turbo-image",
      prompt: "a red fox",
      n: 1,
      size: "512x512",
      response_format: "b64_json",
    });
  });

  it("builds one vision message with the text and every image", () => {
    expect(visionBody("moondream-vision", "Describe.", ["data:image/jpeg;base64,AA", "data:image/jpeg;base64,BB", "data:image/jpeg;base64,CC"])).toEqual({
      model: "moondream-vision",
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: "Describe." },
            { type: "image_url", image_url: { url: "data:image/jpeg;base64,AA" } },
            { type: "image_url", image_url: { url: "data:image/jpeg;base64,BB" } },
            { type: "image_url", image_url: { url: "data:image/jpeg;base64,CC" } },
          ],
        },
      ],
    });
  });
});

describe("frameTimes", () => {
  it("samples the midpoints of equal slices", () => {
    expect(frameTimes(4, 4)).toEqual([0.5, 1.5, 2.5, 3.5]);
    expect(frameTimes(10, 1)).toEqual([5]);
    expect(frameTimes(1, 3)).toEqual([0.167, 0.5, 0.833]);
  });

  it("returns nothing for an empty or unknown duration", () => {
    expect(frameTimes(0, 4)).toEqual([]);
    expect(frameTimes(NaN, 4)).toEqual([]);
    expect(frameTimes(Infinity, 4)).toEqual([]);
    expect(frameTimes(4, 0)).toEqual([]);
  });
});

describe("fitWithin", () => {
  it("scales the long side down to 512 and never up", () => {
    expect(fitWithin(1920, 1080)).toEqual({ width: 512, height: 288 });
    expect(fitWithin(600, 1200)).toEqual({ width: 256, height: 512 });
    expect(fitWithin(320, 240)).toEqual({ width: 320, height: 240 });
  });
});

describe("image nodes at run time", () => {
  it("generates an image from text and decodes the b64 reply", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ data: [{ b64_json: btoa("PNGDATA") }] })));
    const w = TEMPLATES.imageFromText.build();
    w.nodes[1].config.model = "sd-turbo-image";
    const runs = await runWorkflow(w, { baseUrl: "http://x/", token: "t", fetch: fetchMock as unknown as typeof fetch, inputs: {} });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://x/v1/images/generations");
    expect(new Headers(init.headers).get("x-sandbox")).toBe("true");
    expect(JSON.parse(init.body as string).prompt).toBe("A red fox sitting in a snowy forest at sunrise");
    const out = runs["imageOutput-3"].output;
    expect(out?.type).toBe("image");
    expect(out?.type === "image" && (await out.blobs[0].text())).toBe("PNGDATA");
    expect(runs["imageGeneration-2"].ms).toBeGreaterThanOrEqual(0);
  });

  it("asks for an upload before running an image input", async () => {
    const runs = await runWorkflow(wf({ img: "imageInput" }), { baseUrl: "", token: null, fetch: vi.fn() as unknown as typeof fetch, inputs: {} });
    expect(runs.img.error).toBe("Upload an image first");
  });
});

describe("vision model choice", () => {
  const field = NODE_KINDS.vision.fields[0];
  if (field.type !== "model") throw new Error("vision needs a model field");

  it("prefers vision model names among inference deployments", () => {
    const deps = [dep("qwen2-5-1-5b", "inference"), dep("moondream-vision", "inference"), dep("llava-7b", "inference"), dep("sd-turbo-image", "image_generation")];
    expect(matchingDeployments(field, deps).map((d) => d.modelName)).toEqual(["moondream-vision", "llava-7b"]);
  });

  it("ranks vision models last for the plain LLM field", () => {
    const llm = NODE_KINDS.llm.fields[0];
    if (llm.type !== "model") throw new Error("llm needs a model field");
    const deps = [dep("moondream-vision", "inference"), dep("qwen2-5-1-5b", "inference")];
    expect(matchingDeployments(llm, deps).map((d) => d.modelName)).toEqual(["qwen2-5-1-5b", "moondream-vision"]);
  });

  it("recommends the catalog models when nothing matches", () => {
    expect(nodeReadiness({ kind: "vision", config: {} }, [dep("qwen2-5-1-5b", "inference")])).toEqual({ state: "needsDeployment", recommended: MODEL_CATALOG.vision });
    expect(MODEL_CATALOG.vision).toMatchObject({ modelId: "moondream", engine: "ollama", modelType: "inference", deploymentName: "moondream-vision" });
    expect(MODEL_CATALOG.imageGeneration).toMatchObject({ modelId: "stabilityai/sd-turbo", engine: "inferia-diffusion", modelType: "image_generation", deploymentName: "sd-turbo-image" });
    expect(MODEL_CATALOG.imageGeneration!.job).toMatchObject({ image: "docker.io/inferiaai/inferiadiffusion:latest", gpu: true });
  });
});

describe("templates", () => {
  it("chains the image and video templates", () => {
    expect(kinds(TEMPLATES.imageFromText.build())).toEqual(["textInput", "imageGeneration", "imageOutput"]);
    expect(kinds(TEMPLATES.describeVideo.build())).toEqual(["videoInput", "vision", "textOutput"]);
    expect(kinds(TEMPLATES.spokenImage.build())).toEqual(["audioInput", "speechToText", "imageGeneration", "imageOutput"]);
    expect(TEMPLATES.describeVideo.build().nodes[1].config.prompt).toBe("Describe what happens across these frames.");
  });
});

describe("planner", () => {
  it("lists the image kinds in the system prompt", () => {
    const prompt = plannerPrompt();
    expect(prompt).toContain("- imageGeneration: Generate an image from a text prompt. Input: text. Output: image. Config: none.");
    expect(prompt).toContain("- vision: Describe an image or video frames with a vision model. Input: image. Output: text. Config: prompt (prompt).");
    expect(prompt).toContain("- videoInput: Upload a short video, sampled into evenly spaced frames. Input: none. Output: image. Config: frames (frames).");
  });

  it("falls back to image flows by keyword", () => {
    expect(kinds(planFromKeywords("Draw a picture of a cat"))).toEqual(["textInput", "imageGeneration", "imageOutput"]);
    expect(kinds(planFromKeywords("Generate an image for my blog"))).toEqual(["textInput", "imageGeneration", "imageOutput"]);
    expect(kinds(planFromKeywords("Summarize a video clip"))).toEqual(["videoInput", "vision", "textOutput"]);
    expect(kinds(planFromKeywords("Tell me what's in my photo"))).toEqual(["imageInput", "vision", "textOutput"]);
    expect(kinds(planFromKeywords("Look at a screenshot"))).toEqual(["imageInput", "vision", "textOutput"]);
    expect(kinds(planFromKeywords("Speak a request and get an image back"))).toEqual(["audioInput", "speechToText", "imageGeneration", "imageOutput"]);
    expect(kinds(planFromKeywords("A voice assistant for my bank"))).toEqual(["audioInput", "speechToText", "llm", "textToSpeech", "audioOutput"]);
  });

  it("requires the named image modality from a model plan", () => {
    expect(intentError("describe this video", TEMPLATES.imageFromText.build())).toBe("The request needs videoInput and textOutput");
    expect(intentError("draw a picture", TEMPLATES.imageFromText.build())).toBeNull();
  });
});
