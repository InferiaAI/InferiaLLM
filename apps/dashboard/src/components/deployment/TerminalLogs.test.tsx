/**
 * Tests for TerminalLogs.tsx
 *
 * Focus: the WS URL resolution logic is robust to a relative API_GATEWAY_URL.
 * The gateway's `/deployment/logs/{id}/stream` endpoint is mocked to return
 * various `ws_url` values; we verify the resulting absolute URL passed to
 * `new WebSocket(url)`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";

// ── WebSocket stub ────────────────────────────────────────────────────────────
const capturedWsUrls: string[] = [];

class MockWebSocket {
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    capturedWsUrls.push(url);
  }

  close() {}
  send() {}
}

// ── Token stub ────────────────────────────────────────────────────────────────
vi.mock("@/lib/tokenStore", () => ({
  getToken: () => "tok",
  setToken: vi.fn(),
  clearToken: vi.fn(),
  getRefreshToken: () => null,
  setRefreshToken: vi.fn(),
}));

// ── computeApi stub ───────────────────────────────────────────────────────────
// Default: returns a relative ws_url to exercise the toWsUrl path.
let mockWsUrl = "/v1/workers/dep-1/logs/stream";
const mockSubscription = { type: "subscribe", deploymentId: "dep-1" };
let mockApiError: string | null = null;

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual("@/lib/api") as Record<string, unknown>;
  return {
    ...actual,
    computeApi: {
      get: vi.fn(async (path: string) => {
        if (mockApiError) throw new Error(mockApiError);
        if (path.includes("/stream")) {
          return { data: { ws_url: mockWsUrl, subscription: mockSubscription } };
        }
        // persisted logs fallback
        return { data: { logs: [] } };
      }),
    },
  };
});

// ── Tests ────────────────────────────────────────────────────────────────────

describe("TerminalLogs — WS URL resolution", () => {
  let origWS: typeof WebSocket;

  beforeEach(async () => {
    origWS = window.WebSocket;
    window.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    capturedWsUrls.length = 0;
    mockApiError = null;
    mockWsUrl = "/v1/workers/dep-1/logs/stream";
  });

  afterEach(() => {
    window.WebSocket = origWS;
  });

  it("builds an absolute ws:// URL from a relative ws_url starting with /", async () => {
    const { default: TerminalLogs } = await import("./TerminalLogs");

    await act(async () => {
      render(<TerminalLogs deploymentId="dep-1" />);
      // Allow promises in the connect() async function to resolve
      await new Promise(r => setTimeout(r, 0));
    });

    expect(capturedWsUrls.length).toBeGreaterThan(0);
    const url = capturedWsUrls[capturedWsUrls.length - 1];
    expect(url).toMatch(/^wss?:\/\//);
    expect(url).not.toMatch(/^\/v1/);
  });

  it("relative ws_url produces URL containing the path", async () => {
    mockWsUrl = "/v1/workers/dep-1/logs/stream";
    const { default: TerminalLogs } = await import("./TerminalLogs");

    await act(async () => {
      render(<TerminalLogs deploymentId="dep-1" />);
      await new Promise(r => setTimeout(r, 0));
    });

    const url = capturedWsUrls[capturedWsUrls.length - 1];
    // The resolved URL must contain the path from the relative ws_url
    expect(url).toContain("/v1/workers/dep-1/logs/stream");
  });

  it("absolute ws:// ws_url is used as-is (no double-conversion)", async () => {
    mockWsUrl = "ws://some-backend:9000/v1/logs";
    const { default: TerminalLogs } = await import("./TerminalLogs");

    await act(async () => {
      render(<TerminalLogs deploymentId="dep-1" />);
      await new Promise(r => setTimeout(r, 0));
    });

    const url = capturedWsUrls[capturedWsUrls.length - 1];
    expect(url).toMatch(/^ws:\/\/some-backend:9000\//);
  });

  it("absolute wss:// ws_url is used as-is", async () => {
    mockWsUrl = "wss://secure-host/logs";
    const { default: TerminalLogs } = await import("./TerminalLogs");

    await act(async () => {
      render(<TerminalLogs deploymentId="dep-1" />);
      await new Promise(r => setTimeout(r, 0));
    });

    const url = capturedWsUrls[capturedWsUrls.length - 1];
    expect(url).toMatch(/^wss:\/\/secure-host\//);
  });

  it("http:// ws_url is converted to ws://", async () => {
    mockWsUrl = "http://legacy-host:8080/ws/logs";
    const { default: TerminalLogs } = await import("./TerminalLogs");

    await act(async () => {
      render(<TerminalLogs deploymentId="dep-1" />);
      await new Promise(r => setTimeout(r, 0));
    });

    const url = capturedWsUrls[capturedWsUrls.length - 1];
    expect(url).toMatch(/^ws:\/\/legacy-host:8080\//);
  });

  it("https:// ws_url is converted to wss://", async () => {
    mockWsUrl = "https://secure-legacy/ws";
    const { default: TerminalLogs } = await import("./TerminalLogs");

    await act(async () => {
      render(<TerminalLogs deploymentId="dep-1" />);
      await new Promise(r => setTimeout(r, 0));
    });

    const url = capturedWsUrls[capturedWsUrls.length - 1];
    expect(url).toMatch(/^wss:\/\/secure-legacy\//);
  });

  it("JWT token is appended as query param to relative ws_url", async () => {
    mockWsUrl = "/v1/workers/dep-1/logs/stream";
    const { default: TerminalLogs } = await import("./TerminalLogs");

    await act(async () => {
      render(<TerminalLogs deploymentId="dep-1" />);
      await new Promise(r => setTimeout(r, 0));
    });

    const url = capturedWsUrls[capturedWsUrls.length - 1];
    expect(url).toContain("token=tok");
  });
});
