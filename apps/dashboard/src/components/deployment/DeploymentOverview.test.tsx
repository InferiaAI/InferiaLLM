/**
 * Tests for DeploymentOverview.tsx
 *
 * Focus: the displayed public inference endpoint is absolute (http(s)://.../inf/v1/...)
 * even when INFERENCE_URL is a relative path like "/inf".
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ── Helpers ────────────────────────────────────────────────────────────────────

function setRuntimeConfig(cfg: Record<string, unknown>): void {
  (window as unknown as { __RUNTIME_CONFIG__?: unknown }).__RUNTIME_CONFIG__ = cfg;
}
function clearRuntimeConfig(): void {
  delete (window as unknown as { __RUNTIME_CONFIG__?: unknown }).__RUNTIME_CONFIG__;
}

// Minimal deployment object for a standard chat/inference deployment
const baseDeployment = {
  id: "dep-001",
  model_name: "llama3",
  provider: "aws",
  state: "RUNNING",
  engine: "ollama",
  created_at: "2026-01-01T00:00:00Z",
};

// ── Mock @/lib/api to control INFERENCE_URL ───────────────────────────────────
// The module-level const INFERENCE_URL is captured at import time, so we mock
// the entire module with different values per describe block.

describe("DeploymentOverview — endpoint display with default INFERENCE_URL", () => {
  it("shows the chat completions endpoint", async () => {
    const { default: DeploymentOverview } = await import("./DeploymentOverview");
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <DeploymentOverview deployment={baseDeployment} />
      </QueryClientProvider>
    );
    // The /v1/chat/completions path should be visible somewhere
    expect(screen.getAllByText(/\/v1\/chat\/completions/i).length).toBeGreaterThan(0);
  });
});

describe("DeploymentOverview — absolute endpoint URL with relative INFERENCE_URL (/inf)", () => {
  beforeEach(() => {
    vi.resetModules();
    setRuntimeConfig({ INFERENCE_URL: "/inf" });
  });

  afterEach(() => {
    vi.resetModules();
    clearRuntimeConfig();
  });

  it("displays an absolute http(s):// URL for the inference endpoint", async () => {
    const { default: DeploymentOverview } = await import("./DeploymentOverview");

    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <DeploymentOverview deployment={baseDeployment} />
      </QueryClientProvider>
    );

    // Find the text containing /v1/chat/completions
    const endpointEl = screen.getByText(/\/v1\/chat\/completions/i);
    const displayedUrl = endpointEl.textContent ?? "";

    // Must start with http:// or https:// — not a bare /inf/... relative path
    expect(displayedUrl).toMatch(/^https?:\/\//);
    // Must include /inf (the INFERENCE_URL base)
    expect(displayedUrl).toContain("/inf");
    // Must include the completions path
    expect(displayedUrl).toContain("/v1/chat/completions");
  });

  it("displays an absolute URL that is copy-pasteable (includes host)", async () => {
    const { default: DeploymentOverview } = await import("./DeploymentOverview");

    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <DeploymentOverview deployment={baseDeployment} />
      </QueryClientProvider>
    );

    const endpointEl = screen.getByText(/\/v1\/chat\/completions/i);
    const displayedUrl = endpointEl.textContent ?? "";

    // URL must have a host segment (not just a path)
    const parsed = new URL(displayedUrl);
    expect(parsed.host).toBeTruthy();
    expect(parsed.pathname).toContain("/inf/v1/chat/completions");
  });
});

describe("DeploymentOverview — absolute endpoint URL with absolute INFERENCE_URL", () => {
  beforeEach(() => {
    vi.resetModules();
    setRuntimeConfig({ INFERENCE_URL: "http://inf.example.com:8001" });
  });

  afterEach(() => {
    vi.resetModules();
    clearRuntimeConfig();
  });

  it("preserves the absolute host when INFERENCE_URL is already absolute", async () => {
    const { default: DeploymentOverview } = await import("./DeploymentOverview");

    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <DeploymentOverview deployment={baseDeployment} />
      </QueryClientProvider>
    );

    const endpointEl = screen.getByText(/\/v1\/chat\/completions/i);
    const displayedUrl = endpointEl.textContent ?? "";

    expect(displayedUrl).toContain("inf.example.com:8001");
    expect(displayedUrl).toContain("/v1/chat/completions");
  });
});

describe("DeploymentOverview — embedding endpoint display with relative INFERENCE_URL", () => {
  beforeEach(() => {
    vi.resetModules();
    setRuntimeConfig({ INFERENCE_URL: "/inf" });
  });

  afterEach(() => {
    vi.resetModules();
    clearRuntimeConfig();
  });

  it("shows absolute URL for embedding endpoint when engine=infinity", async () => {
    const { default: DeploymentOverview } = await import("./DeploymentOverview");
    const embeddingDeployment = {
      ...baseDeployment,
      engine: "infinity",
      model_type: "embedding",
    };

    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <DeploymentOverview deployment={embeddingDeployment} />
      </QueryClientProvider>
    );

    // The embedding completions URL should be absolute
    const endpointEl = screen.getByText(/\/v1\/embeddings/i);
    const displayedUrl = endpointEl.textContent ?? "";
    expect(displayedUrl).toMatch(/^https?:\/\//);
    expect(displayedUrl).toContain("/inf/v1/embeddings");
  });
});
