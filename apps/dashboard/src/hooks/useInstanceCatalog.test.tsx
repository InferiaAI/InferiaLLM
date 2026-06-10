/**
 * Tests for the useInstanceCatalog hook.
 *
 * Scope:
 *   - No-arg call: fetches the curated catalog via computeApi.get (axios, NOT fetch).
 *   - Returns the catalog grouped by class with the price_per_hour wire field.
 *   - Surfaces network/HTTP errors via the query's `error` state instead of
 *     swallowing them silently.
 *   - Region-aware: when region provided + live returns real data (fallback=false),
 *     use grouped live catalog (gpu_ram_gb/price_per_hour default to 0).
 *   - Fallback: when live call returns fallback=true or empty list, falls back to curated.
 *   - No-region path still works unchanged (backward-compat).
 *   - queryKey isolation: different regions produce distinct cached results.
 *
 * Mock strategy:
 *   The hook uses `computeApi.get(...)` (axios-based), NOT `globalThis.fetch`.
 *   We therefore module-mock `@/lib/api` and drive `computeApi.get` as a vi.fn().
 *   ConfigService.listAwsInstanceTypes is spied on via vi.spyOn for the live path.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

// ---------------------------------------------------------------------------
// Module mock — intercepts the curated path (computeApi.get)
// ---------------------------------------------------------------------------
// Must appear before any imports that transitively touch @/lib/api.
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    // Override only the clients the hook touches; keep everything else real.
    computeApi: { get: vi.fn() },
    default: { get: vi.fn(), post: vi.fn() },
  };
});

import { computeApi } from "@/lib/api";
import { useInstanceCatalog } from "./useInstanceCatalog";
import * as configServiceModule from "@/services/configService";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeWrapper() {
  // Disable retries so a single failed fetch surfaces immediately.
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CURATED_FIXTURE = {
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

// A second fixture with different price values, used for the queryKey isolation test.
const CURATED_FIXTURE_ALT = {
  normal_gpu: [
    {
      name: "g5.xlarge",
      cls: "normal_gpu",
      vcpu: 4,
      ram_gb: 16,
      gpu_count: 1,
      gpu_model: "NVIDIA A10G",
      gpu_ram_gb: 24,
      price_per_hour: 1.006,
    },
  ],
  heavy_gpu: [],
  cpu: [],
};

const LIVE_TYPES = [
  { instance_type: "g6.xlarge", vcpus: 4, memory_gb: 16, gpu_count: 1, gpu_model: "NVIDIA L4", is_gpu: true },
  { instance_type: "p4d.24xlarge", vcpus: 96, memory_gb: 1152, gpu_count: 8, gpu_model: "NVIDIA A100", is_gpu: true },
  { instance_type: "c6i.xlarge", vcpus: 4, memory_gb: 8, gpu_count: 0, gpu_model: null, is_gpu: false },
];

afterEach(() => {
  vi.restoreAllMocks();
  // Reset the computeApi mock between tests so state doesn't bleed.
  vi.mocked(computeApi.get).mockReset();
});

// ---------------------------------------------------------------------------
// Curated path (no region)
// ---------------------------------------------------------------------------

describe("useInstanceCatalog (no-arg / curated path)", () => {
  it("fetches and groups by class", async () => {
    vi.mocked(computeApi.get).mockResolvedValue({ data: CURATED_FIXTURE });

    const { result } = renderHook(() => useInstanceCatalog(), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(computeApi.get).toHaveBeenCalledWith("/providers/aws/instance-catalog");
    expect(result.current.data?.normal_gpu[0].name).toBe("g6.xlarge");
    expect(result.current.data?.normal_gpu[0].price_per_hour).toBe(0.8);
    expect(result.current.data?.cpu[0].gpu_count).toBe(0);
    expect(result.current.data?.cpu[0].gpu_model).toBeNull();
    expect(result.current.data?.heavy_gpu).toEqual([]);
  });

  it("surfaces HTTP errors as query errors (not silently swallowed)", async () => {
    // Axios rejects on non-2xx; the rejection propagates through useQuery.
    const axiosError = Object.assign(new Error("Request failed with status code 500"), {
      response: { status: 500, data: "server boom" },
    });
    vi.mocked(computeApi.get).mockRejectedValue(axiosError);

    const { result } = renderHook(() => useInstanceCatalog(), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    // The error object is the axios error itself — assert it is an Error instance
    // and that it carries a recognisable status indicator.
    expect(result.current.error).toBeInstanceOf(Error);
    expect((result.current.error as any).response?.status).toBe(500);
    expect(result.current.data).toBeUndefined();
  });

  it("surfaces network failures (computeApi.get rejects) as query errors", async () => {
    // Simulate axios network-level failure (no response object).
    const netError = Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" });
    vi.mocked(computeApi.get).mockRejectedValue(netError);

    const { result } = renderHook(() => useInstanceCatalog(), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
    expect((result.current.error as Error).message).toMatch(/Network Error/);
  });
});

// ---------------------------------------------------------------------------
// Region-aware live path
// ---------------------------------------------------------------------------

describe("useInstanceCatalog (region-aware live path)", () => {
  it("uses live catalog when region provided and fallback=false with data", async () => {
    vi.spyOn(configServiceModule.ConfigService, "listAwsInstanceTypes").mockResolvedValue({
      instance_types: LIVE_TYPES,
      fallback: false,
    });

    const { result } = renderHook(() => useInstanceCatalog("us-east-1"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.data).toBeDefined());

    // single-GPU → normal_gpu
    expect(result.current.data?.normal_gpu).toHaveLength(1);
    expect(result.current.data?.normal_gpu[0].name).toBe("g6.xlarge");
    expect(result.current.data?.normal_gpu[0].gpu_count).toBe(1);
    expect(result.current.data?.normal_gpu[0].gpu_model).toBe("NVIDIA L4");
    // live discovery has no pricing/VRAM
    expect(result.current.data?.normal_gpu[0].gpu_ram_gb).toBe(0);
    expect(result.current.data?.normal_gpu[0].price_per_hour).toBe(0);

    // multi-GPU → heavy_gpu
    expect(result.current.data?.heavy_gpu).toHaveLength(1);
    expect(result.current.data?.heavy_gpu[0].name).toBe("p4d.24xlarge");
    expect(result.current.data?.heavy_gpu[0].gpu_count).toBe(8);

    // no-GPU → cpu
    expect(result.current.data?.cpu).toHaveLength(1);
    expect(result.current.data?.cpu[0].name).toBe("c6i.xlarge");
    expect(result.current.data?.cpu[0].gpu_count).toBe(0);
    expect(result.current.data?.cpu[0].gpu_model).toBeNull();

    // curated endpoint must NOT have been called when live succeeds
    expect(computeApi.get).not.toHaveBeenCalled();
  });

  it("falls back to curated when live returns fallback=true", async () => {
    vi.spyOn(configServiceModule.ConfigService, "listAwsInstanceTypes").mockResolvedValue({
      instance_types: LIVE_TYPES,
      fallback: true,
    });
    vi.mocked(computeApi.get).mockResolvedValue({ data: CURATED_FIXTURE });

    const { result } = renderHook(() => useInstanceCatalog("eu-west-1"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.data).toBeDefined());

    // curated catalog has price_per_hour values; live doesn't
    expect(result.current.data?.normal_gpu[0].price_per_hour).toBe(0.8);
    expect(computeApi.get).toHaveBeenCalledWith("/providers/aws/instance-catalog");
  });

  it("falls back to curated when live returns empty list", async () => {
    vi.spyOn(configServiceModule.ConfigService, "listAwsInstanceTypes").mockResolvedValue({
      instance_types: [],
      fallback: false,
    });
    vi.mocked(computeApi.get).mockResolvedValue({ data: CURATED_FIXTURE });

    const { result } = renderHook(() => useInstanceCatalog("ap-southeast-1"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(result.current.data?.normal_gpu[0].price_per_hour).toBe(0.8);
    expect(computeApi.get).toHaveBeenCalledWith("/providers/aws/instance-catalog");
  });

  it("does NOT call ConfigService when no region supplied (backward-compat)", async () => {
    const listSpy = vi.spyOn(configServiceModule.ConfigService, "listAwsInstanceTypes");
    vi.mocked(computeApi.get).mockResolvedValue({ data: CURATED_FIXTURE });

    const { result } = renderHook(() => useInstanceCatalog(), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(listSpy).not.toHaveBeenCalled();
    expect(computeApi.get).toHaveBeenCalledWith("/providers/aws/instance-catalog");
    // curated catalog price values are present
    expect(result.current.data?.normal_gpu[0].price_per_hour).toBe(0.8);
  });

  it("queryKey includes region so different regions return distinct cached data", async () => {
    // Two hooks share one QueryClient but different regions → different query keys
    // → TanStack Query keeps them in separate cache slots.
    const listSpy = vi
      .spyOn(configServiceModule.ConfigService, "listAwsInstanceTypes")
      .mockImplementation(async (region: string) => {
        // Return distinct live types per region so data differs.
        if (region === "us-east-1") {
          return {
            instance_types: [
              { instance_type: "g6.xlarge", vcpus: 4, memory_gb: 16, gpu_count: 1, gpu_model: "NVIDIA L4", is_gpu: true },
            ],
            fallback: false,
          };
        }
        // us-west-2 → fallback=true so it hits computeApi (curated path).
        return { instance_types: [], fallback: true };
      });

    // us-west-2 fallback path will call computeApi.get → return the alt fixture.
    vi.mocked(computeApi.get).mockResolvedValue({ data: CURATED_FIXTURE_ALT });

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const { result: r1 } = renderHook(() => useInstanceCatalog("us-east-1"), { wrapper });
    const { result: r2 } = renderHook(() => useInstanceCatalog("us-west-2"), { wrapper });

    await waitFor(() => {
      expect(r1.current.data).toBeDefined();
      expect(r2.current.data).toBeDefined();
    });

    // us-east-1 → live path → price_per_hour is 0 (live discovery has no pricing)
    expect(r1.current.data?.normal_gpu[0].name).toBe("g6.xlarge");
    expect(r1.current.data?.normal_gpu[0].price_per_hour).toBe(0);

    // us-west-2 → curated fallback → alt fixture → g5.xlarge with price 1.006
    expect(r2.current.data?.normal_gpu[0].name).toBe("g5.xlarge");
    expect(r2.current.data?.normal_gpu[0].price_per_hour).toBe(1.006);

    // Confirm both spies/mocks were exercised
    expect(listSpy).toHaveBeenCalledWith("us-east-1");
    expect(listSpy).toHaveBeenCalledWith("us-west-2");
    expect(computeApi.get).toHaveBeenCalledWith("/providers/aws/instance-catalog");
  });
});
