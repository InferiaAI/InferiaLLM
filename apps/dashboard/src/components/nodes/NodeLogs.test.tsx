import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import NodeLogs from "./NodeLogs";

vi.mock("@/services/provisioningService", () => ({
  getProvisioningLogs: vi.fn(),
  getEC2Console:       vi.fn(),
}));

const { getProvisioningLogs, getEC2Console } =
  await import("@/services/provisioningService");

// ── AWS provisioning mode tests (poll REST, no WebSocket) ─────────────────────
describe("NodeLogs (AWS provisioning mode)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(getProvisioningLogs).mockResolvedValue({
      events: [
        { id: 1, phase: "pulumi_up", status: "log",
          message: "create ec2", created_at: "2026-05-25T00:00:00Z" },
      ],
      next_after: 1,
    } as Awaited<ReturnType<typeof getProvisioningLogs>>);
  });
  afterEach(() => vi.useRealTimers());

  it("polls /provisioning-logs every 2s when provider=aws and state=provisioning", async () => {
    render(<NodeLogs nodeId="n1" nodeProvider="aws" nodeState="provisioning" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getProvisioningLogs).toHaveBeenCalledWith("n1", 0);
    expect(screen.getByText(/create ec2/)).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(getProvisioningLogs).toHaveBeenLastCalledWith("n1", 1);
  });

  it("does not poll provisioning when state='ready' (delegates to WS path)", async () => {
    render(<NodeLogs nodeId="n1" nodeProvider="aws" nodeState="ready" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(getProvisioningLogs).not.toHaveBeenCalled();
  });

  it("fetches EC2 console when the user clicks the button", async () => {
    vi.mocked(getEC2Console).mockResolvedValue({
      logs: ["[boot] cloud-init"], fetched_at: "2026-05-25T00:00:00Z",
    });
    render(<NodeLogs nodeId="n1" nodeProvider="aws" nodeState="provisioning" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const btn = screen.getByRole("button", { name: /fetch ec2 console/i });
    await act(async () => {
      btn.click();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(getEC2Console).toHaveBeenCalledWith("n1");
    expect(screen.getByText(/cloud-init/)).toBeInTheDocument();
  });
});

// ── WS URL tests (NodeLogsWS path) ────────────────────────────────────────────
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

vi.mock("@/lib/tokenStore", () => ({
  getToken: () => "test-token",
  setToken: vi.fn(),
  clearToken: vi.fn(),
  getRefreshToken: () => null,
  setRefreshToken: vi.fn(),
}));

describe("NodeLogs — WS URL is absolute (NodeLogsWS path)", () => {
  let origWS: typeof WebSocket;

  beforeEach(() => {
    origWS = window.WebSocket;
    window.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    capturedWsUrls.length = 0;
  });

  afterEach(() => {
    window.WebSocket = origWS;
  });

  it("builds an absolute ws(s):// URL on mount (non-AWS path)", () => {
    render(<NodeLogs nodeId="node-log-test" />);

    expect(capturedWsUrls.length).toBeGreaterThan(0);
    const url = capturedWsUrls[capturedWsUrls.length - 1];
    // Must be absolute
    expect(url).toMatch(/^wss?:\/\//);
    // Must NOT be a bare relative path
    expect(url).not.toMatch(/^\/api/);
  });

  it("includes /v1/admin/workers/{nodeId}/logs in the URL", () => {
    render(<NodeLogs nodeId="node-log-test" />);

    const url = capturedWsUrls[capturedWsUrls.length - 1];
    expect(url).toContain("/v1/admin/workers/node-log-test/logs");
  });

  it("includes the access_token query param", () => {
    render(<NodeLogs nodeId="node-log-test" />);

    const url = capturedWsUrls[capturedWsUrls.length - 1];
    expect(url).toContain("access_token=test-token");
  });

  it("includes optional deploymentId as query param when provided", () => {
    render(<NodeLogs nodeId="n1" deploymentId="deploy-42" />);

    const url = capturedWsUrls[capturedWsUrls.length - 1];
    expect(url).toContain("deployment=deploy-42");
  });

  it("includes optional containerId as query param when provided", () => {
    render(<NodeLogs nodeId="n1" containerId="ctr-abc" />);

    const url = capturedWsUrls[capturedWsUrls.length - 1];
    expect(url).toContain("container=ctr-abc");
  });

  it("URL reconnects with new absolute URL when nodeId changes", () => {
    const { rerender } = render(<NodeLogs nodeId="n1" />);
    const firstLen = capturedWsUrls.length;

    rerender(<NodeLogs nodeId="n2" />);

    // A new WS connection should have been opened for n2
    expect(capturedWsUrls.length).toBeGreaterThan(firstLen);
    const lastUrl = capturedWsUrls[capturedWsUrls.length - 1];
    expect(lastUrl).toMatch(/^wss?:\/\//);
    expect(lastUrl).toContain("n2");
  });

  it("does NOT open a WS connection for AWS provisioning nodes (uses polling instead)", () => {
    render(<NodeLogs nodeId="n1" nodeProvider="aws" nodeState="provisioning" />);
    // No WS URL should be captured — AWS provisioning uses REST polling
    expect(capturedWsUrls.length).toBe(0);
  });
});
