/**
 * Tests for the useInstanceCatalog hook.
 *
 * Scope:
 *   - Calls the correct endpoint (/api/v1/providers/aws/instance-catalog).
 *   - Returns the catalog grouped by class with the price_per_hour wire field.
 *   - Surfaces network/HTTP errors via the query's `error` state instead of
 *     swallowing them silently.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { useInstanceCatalog } from "./useInstanceCatalog";

function makeWrapper() {
  // Disable retries so a single failed fetch surfaces immediately
  // (the default 3-retry loop would slow the error test to a crawl).
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

const FIXTURE = {
  normal_gpu: [
    {
      name: "g6.xlarge",
      cls: "normal_gpu",
      vcpu: 4,
      ram_gb: 16,
      gpu_count: 1,
      gpu_model: "NVIDIA L4",
      gpu_ram_gb: 24,
      price_per_hour: 0.8,
    },
  ],
  heavy_gpu: [],
  cpu: [
    {
      name: "c6i.xlarge",
      cls: "cpu",
      vcpu: 4,
      ram_gb: 8,
      gpu_count: 0,
      gpu_model: null,
      gpu_ram_gb: 0,
      price_per_hour: 0.17,
    },
  ],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useInstanceCatalog", () => {
  it("fetches and groups by class", async () => {
    const fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify(FIXTURE), { status: 200 }),
    );

    const { result } = renderHook(() => useInstanceCatalog(), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(fetchSpy).toHaveBeenCalledWith("/api/v1/providers/aws/instance-catalog");
    expect(result.current.data?.normal_gpu[0].name).toBe("g6.xlarge");
    expect(result.current.data?.normal_gpu[0].price_per_hour).toBe(0.8);
    expect(result.current.data?.cpu[0].gpu_count).toBe(0);
    expect(result.current.data?.cpu[0].gpu_model).toBeNull();
    expect(result.current.data?.heavy_gpu).toEqual([]);
  });

  it("surfaces HTTP errors as query errors (not silently swallowed)", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response("server boom", { status: 500 }),
    );

    const { result } = renderHook(() => useInstanceCatalog(), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as Error).message).toMatch(/catalog fetch failed: 500/);
    expect(result.current.data).toBeUndefined();
  });

  it("surfaces network failures (fetch rejects) as query errors", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new TypeError("offline"));

    const { result } = renderHook(() => useInstanceCatalog(), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as Error).message).toMatch(/offline/);
  });
});
