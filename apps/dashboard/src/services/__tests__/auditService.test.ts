import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * auditService.getLogs must only ever resolve with an array. When an edge
 * proxy answers an API path with the SPA's index.html fallback (seen in the
 * field: a reverse-proxy matcher missing /audit/*), axios resolves with an
 * HTML string body — without the guard the page crashed inside
 * `logs.map(...)` ("e.map is not a function").
 */
const apiGet = vi.fn();
vi.mock("@/lib/api", () => ({
  default: { get: (...args: unknown[]) => apiGet(...args) },
}));

import { auditService, type AuditLog } from "@/services/auditService";

const sampleLog: AuditLog = {
  id: "log-1",
  timestamp: "2026-06-10T12:00:00Z",
  user_id: "user-1",
  user_email: "owner@acme.test",
  action: "login",
  category: "auth",
  resource_type: null,
  resource_id: null,
  details: null,
  ip_address: "127.0.0.1",
  status: "success",
};

describe("auditService.getLogs", () => {
  beforeEach(() => {
    apiGet.mockReset();
  });

  it("returns the array payload as-is", async () => {
    apiGet.mockResolvedValue({ data: [sampleLog] });
    await expect(auditService.getLogs()).resolves.toEqual([sampleLog]);
  });

  it("returns an empty array payload as-is", async () => {
    apiGet.mockResolvedValue({ data: [] });
    await expect(auditService.getLogs()).resolves.toEqual([]);
  });

  it("throws when the body is an HTML string (SPA fallback)", async () => {
    apiGet.mockResolvedValue({ data: "<!doctype html><html>...</html>" });
    await expect(auditService.getLogs()).rejects.toThrow(
      /Unexpected response from \/audit\/logs/,
    );
  });

  it("throws when the body is a JSON object instead of a list", async () => {
    apiGet.mockResolvedValue({ data: { detail: "Permission denied" } });
    await expect(auditService.getLogs()).rejects.toThrow(
      /Unexpected response from \/audit\/logs/,
    );
  });

  it("throws when the body is null", async () => {
    apiGet.mockResolvedValue({ data: null });
    await expect(auditService.getLogs()).rejects.toThrow(
      /Unexpected response from \/audit\/logs/,
    );
  });

  it("forwards filters and pagination as query params", async () => {
    apiGet.mockResolvedValue({ data: [] });
    await auditService.getLogs({ category: "auth" }, { skip: 10, limit: 50 });
    const [path, opts] = apiGet.mock.calls[0] as [string, { params: URLSearchParams }];
    expect(path).toBe("/audit/logs");
    expect(opts.params.get("category")).toBe("auth");
    expect(opts.params.get("skip")).toBe("10");
    expect(opts.params.get("limit")).toBe("50");
  });
});
