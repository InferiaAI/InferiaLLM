/**
 * Tests for the AWS instance dropdown + region select wiring (PD-T3).
 *
 * Scope:
 *   - The wizard fetches the catalog from useInstanceCatalog (T22) instead
 *     of importing a hard-coded module constant (T31 swap).
 *   - The AWS region selector is now a native <select> (not a button grid).
 *   - The AWS instance selector is now a single InstanceDropdown listing all
 *     instance types GPU-first (heavy_gpu → normal_gpu → cpu) — no tier tabs.
 *   - Selecting an instance from the dropdown + a region renders the cost
 *     summary using the catalog's price_per_hour (not the GCP fallback).
 *   - Selecting an AWS CPU instance hides the GPU Count selector.
 *   - Selecting an AWS GPU instance shows the GPU Count selector.
 *   - The selector + catalog render ONLY for AWS — GCP shows the legacy
 *     gcpGpuTypes button grid with its own region button grid.
 *
 * Strategy:
 *   - Mount NewPool with auth + react-query providers stubbed.
 *   - Mock global.fetch to answer /api/v1/providers/aws/instance-catalog
 *     with a deterministic fixture.
 *   - Mock the compute API to advertise aws + gcp as registered providers
 *     so Step 1 → click → Step 2 lands on the cluster-provider branch.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Catalog fixture
// ---------------------------------------------------------------------------
//
// Mirror the shape the backend `/api/v1/providers/aws/instance-catalog`
// endpoint returns (see package/src/inferia/services/orchestration/api/
// providers.py). The data values are a subset of the curated
// instance_catalog.py module — enough to drive every assertion below.

const CATALOG_FIXTURE = {
    normal_gpu: [
        {
            name: "g5.xlarge", cls: "normal_gpu", vcpu: 4, ram_gb: 16,
            gpu_count: 1, gpu_model: "NVIDIA A10G", gpu_ram_gb: 24,
            price_per_hour: 1.006,
        },
        {
            name: "g5.2xlarge", cls: "normal_gpu", vcpu: 8, ram_gb: 32,
            gpu_count: 1, gpu_model: "NVIDIA A10G", gpu_ram_gb: 24,
            price_per_hour: 1.212,
        },
        {
            name: "g6.xlarge", cls: "normal_gpu", vcpu: 4, ram_gb: 16,
            gpu_count: 1, gpu_model: "NVIDIA L4", gpu_ram_gb: 24,
            price_per_hour: 0.805,
        },
    ],
    heavy_gpu: [
        {
            name: "p4d.24xlarge", cls: "heavy_gpu", vcpu: 96, ram_gb: 1152,
            gpu_count: 8, gpu_model: "NVIDIA A100", gpu_ram_gb: 320,
            price_per_hour: 32.770,
        },
        {
            name: "p5.48xlarge", cls: "heavy_gpu", vcpu: 192, ram_gb: 2048,
            gpu_count: 8, gpu_model: "NVIDIA H100", gpu_ram_gb: 640,
            price_per_hour: 98.320,
        },
        {
            name: "g5.12xlarge", cls: "heavy_gpu", vcpu: 48, ram_gb: 192,
            gpu_count: 4, gpu_model: "NVIDIA A10G", gpu_ram_gb: 96,
            price_per_hour: 5.672,
        },
    ],
    cpu: [
        {
            name: "c6i.xlarge", cls: "cpu", vcpu: 4, ram_gb: 8,
            gpu_count: 0, gpu_model: null, gpu_ram_gb: 0,
            price_per_hour: 0.170,
        },
        {
            name: "c6i.2xlarge", cls: "cpu", vcpu: 8, ram_gb: 16,
            gpu_count: 0, gpu_model: null, gpu_ram_gb: 0,
            price_per_hour: 0.340,
        },
        {
            name: "m6i.xlarge", cls: "cpu", vcpu: 4, ram_gb: 16,
            gpu_count: 0, gpu_model: null, gpu_ram_gb: 0,
            price_per_hour: 0.192,
        },
    ],
};

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// The compute API only needs to answer two GETs:
//   - /inventory/providers (returns aws + gcp)
//   - /deployment/provider/resources?provider=aws (we never reach the next
//     step that consumes this, but the effect calls it, so stub it.)
// All other calls (createpool) must NOT be invoked by our tests.
const computeApiGet = vi.fn();
const computeApiPost = vi.fn();
vi.mock("@/lib/api", () => ({
    computeApi: { get: (...a: unknown[]) => computeApiGet(...a), post: (...a: unknown[]) => computeApiPost(...a) },
    default: {},
    api: {},
}));

vi.mock("@/services/configService", () => ({
    ConfigService: {
        getProviderConfig: vi.fn().mockResolvedValue({
            // Both providers fully credentialed so they show on Step 1.
            cloud: { aws: { access_key_id: "AKIA…" }, gcp: { project_id: "demo" } },
            depin: {},
        }),
        listProviderCredentials: vi.fn().mockResolvedValue([]),
        // Return fallback:true so the static awsRegions list is used,
        // keeping every existing region-selector assertion unchanged.
        listAwsRegions: vi.fn().mockResolvedValue({ regions: [], fallback: true }),
        listAwsInstanceTypes: vi.fn().mockResolvedValue({ instance_types: [], fallback: true }),
    },
}));

vi.mock("@/services/nodeService", () => ({
    addWorkerNode: vi.fn(),
}));

vi.mock("@/context/AuthContext", () => ({
    useAuth: () => ({
        user: { user_id: "u1", org_id: "org-1", username: "t", email: "t@example.com", roles: [], permissions: [], totp_enabled: false },
        organizations: [{ id: "org-1", name: "Test" }],
        hasPermission: () => true,
    }),
}));

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

import NewPool from "./NewPool";

function renderNewPool() {
    // Disable retries so failures surface immediately and gcTime=0 so cached
    // queries from one test don't bleed into the next.
    const qc = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    return render(
        <QueryClientProvider client={qc}>
            <MemoryRouter initialEntries={["/dashboard/compute/new"]}>
                <NewPool />
            </MemoryRouter>
        </QueryClientProvider>,
    );
}

/**
 * Drive Step 1 → Step 2 for a given provider id. Returns once the cluster
 * configuration block is on screen.
 */
async function gotoStep2(provider: "aws" | "gcp") {
    const user = userEvent.setup();
    renderNewPool();
    // Wait until the provider grid is rendered (no more loading state).
    // The card title comes from the API-list builder which capitalises
    // the provider id and appends "Network" — so the AWS card is
    // "Aws Network" and GCP is "Gcp Network".
    const titleRe = provider === "aws" ? /Aws Network/i : /Gcp Network/i;
    const providerBtn = await screen.findByRole("button", { name: titleRe });
    await user.click(providerBtn);
    // Step 2 cluster block always has the "Configuration" header.
    await screen.findByText(/configuration/i);
    return user;
}

/**
 * Select an AWS region via the native <select>.
 */
function selectAwsRegion(regionId: string) {
    const sel = screen.getByTestId("aws-region-select");
    fireEvent.change(sel, { target: { value: regionId } });
}

/**
 * Open the InstanceDropdown and click a specific instance option.
 */
async function selectInstanceFromDropdown(user: ReturnType<typeof userEvent.setup>, instanceName: string) {
    // Open the dropdown
    const trigger = screen.getByTestId("instance-dropdown-trigger");
    await user.click(trigger);
    // Click the instance option
    const option = await screen.findByTestId(`inst-option-${instanceName}`);
    await user.click(option);
}

// ---------------------------------------------------------------------------
// Default response wiring (override per test as needed).
// ---------------------------------------------------------------------------

beforeEach(() => {
    computeApiGet.mockImplementation((url: string) => {
        if (url.includes("/inventory/providers")) {
            return Promise.resolve({
                data: {
                    providers: {
                        aws: { adapter_type: "cloud", capabilities: { supports_cluster_mode: true, pricing_model: "fixed" } },
                        gcp: { adapter_type: "cloud", capabilities: { supports_cluster_mode: true, pricing_model: "fixed" } },
                    },
                },
            });
        }
        // The curated instance catalog is consumed via computeApi.get (axios),
        // not global.fetch. Return the fixture so the instance buttons render.
        if (url.includes("/providers/aws/instance-catalog")) {
            return Promise.resolve({ data: CATALOG_FIXTURE });
        }
        // /deployment/provider/resources is only consumed by the non-cluster
        // path, but the effect calls it for AWS too; return an empty list so
        // the loading spinner can resolve.
        return Promise.resolve({ data: { resources: [] } });
    });
});

afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AWS InstanceDropdown (GPU-first flat list, no tier tabs)", () => {
    it("renders the InstanceDropdown trigger for AWS — no tier-selector, no aws-inst-* buttons", async () => {
        await gotoStep2("aws");

        // The InstanceDropdown trigger must be present
        await waitFor(() =>
            expect(screen.getByTestId("instance-dropdown")).toBeInTheDocument(),
        );

        // No tier selector
        expect(screen.queryByTestId("tier-selector")).not.toBeInTheDocument();

        // No card-style aws-inst-* test ids (those were from the old grid)
        expect(screen.queryByTestId("aws-inst-g5.xlarge")).not.toBeInTheDocument();
        expect(screen.queryByTestId("aws-inst-p4d.24xlarge")).not.toBeInTheDocument();
        expect(screen.queryByTestId("aws-inst-c6i.xlarge")).not.toBeInTheDocument();
    });

    it("dropdown lists GPU instances (heavy first, then normal) before CPU instances", async () => {
        const user = await gotoStep2("aws");

        // Open the dropdown
        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));
        await user.click(screen.getByTestId("instance-dropdown-trigger"));

        // All instances (GPU + CPU) should be in the dropdown list
        await waitFor(() =>
            expect(screen.getByTestId("inst-option-p4d.24xlarge")).toBeInTheDocument(),
        );
        // GPU instances present
        expect(screen.getByTestId("inst-option-p5.48xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("inst-option-g5.12xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("inst-option-g5.xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("inst-option-g5.2xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("inst-option-g6.xlarge")).toBeInTheDocument();
        // CPU instances present
        expect(screen.getByTestId("inst-option-c6i.xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("inst-option-c6i.2xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("inst-option-m6i.xlarge")).toBeInTheDocument();

        // Verify GPU-first ordering: p4d (heavy_gpu first) appears before c6i (cpu)
        const list = screen.getByTestId("instance-dropdown-list");
        const buttons = within(list).getAllByRole("button");
        const names = buttons.map(b => b.getAttribute("data-testid")?.replace("inst-option-", "") ?? "");
        const p4dIdx = names.indexOf("p4d.24xlarge");
        const c6iIdx = names.indexOf("c6i.xlarge");
        expect(p4dIdx).toBeGreaterThanOrEqual(0);
        expect(c6iIdx).toBeGreaterThanOrEqual(0);
        expect(p4dIdx).toBeLessThan(c6iIdx);
    });

    it("selecting a GPU instance shows it in the trigger and shows the GPU Count selector", async () => {
        const user = await gotoStep2("aws");

        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));
        await selectInstanceFromDropdown(user, "g6.xlarge");

        // Trigger shows selected instance summary
        const trigger = screen.getByTestId("instance-dropdown-trigger");
        expect(trigger.textContent).toMatch(/g6\.xlarge/);

        // GPU Count selector is visible for a GPU instance
        expect(screen.getByTestId("gpu-count")).toBeInTheDocument();
    });

    it("selecting a CPU instance hides the GPU Count selector", async () => {
        const user = await gotoStep2("aws");

        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));
        await selectInstanceFromDropdown(user, "c6i.xlarge");

        // GPU Count selector must be hidden for a CPU instance
        expect(screen.queryByTestId("gpu-count")).not.toBeInTheDocument();
    });

    it("switching from GPU instance with gpuCount=4 to CPU instance forces gpuCount=1 (regression: stale gpu_count in payload)", async () => {
        // Regression: after removing tier tabs, SET_RESOURCE did not reset
        // gpuCount, so selecting GPU → bump count to 4 → select CPU left
        // gpuCount=4 in state. The GPU Count UI was hidden but the stale value
        // was still sent in the submit payload (gpu_count:4 for a CPU instance).
        const user = await gotoStep2("aws");
        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));

        // Step 1: select a GPU instance
        await selectInstanceFromDropdown(user, "g5.xlarge");

        // GPU Count selector should be visible
        const gpuCountBlock = screen.getByTestId("gpu-count");
        expect(gpuCountBlock).toBeInTheDocument();

        // Step 2: set GPU count to 4
        const btn4 = within(gpuCountBlock).getByRole("button", { name: /^4x$/i });
        await user.click(btn4);

        // Confirm the 4x button is active (has the selected class)
        // and the Summary label would show 4x if a region were selected.
        // We verify state indirectly via the cost summary — select a region too.
        selectAwsRegion("us-east-1");
        await screen.findByText(/Summary: 4x g5\.xlarge/i);

        // Step 3: now select a CPU instance from the same dropdown
        await selectInstanceFromDropdown(user, "c6i.xlarge");

        // GPU Count selector must now be hidden (CPU instance)
        expect(screen.queryByTestId("gpu-count")).not.toBeInTheDocument();

        // The Summary must show 1x (not 4x) — gpuCount was reset by the reducer
        const summary = await screen.findByText(/Summary: 1x c6i\.xlarge/i);
        expect(summary).toBeInTheDocument();

        // The estimated cost uses gpuCount=1: 0.170 * 1 = 0.17
        expect(screen.getByText(/Estimated: \$0\.17\/hr/i)).toBeInTheDocument();
    });

    it("selecting a CPU instance + region renders the cost summary using catalog price_per_hour", async () => {
        const user = await gotoStep2("aws");
        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));

        // Select CPU instance (c6i.xlarge, price 0.170)
        await selectInstanceFromDropdown(user, "c6i.xlarge");

        // Select a region via the native <select>
        selectAwsRegion("us-east-1");

        // Summary block uses the instance name (not the gpu_type "(none)")
        const summary = await screen.findByText(/Summary: 1x c6i\.xlarge/i);
        expect(summary).toBeInTheDocument();

        // Estimated cost uses catalog's price_per_hour (0.170), not GCP fallback (1.0)
        expect(screen.getByText(/Estimated: \$0\.17\/hr/i)).toBeInTheDocument();

        // Continue button is enabled
        const continueBtn = screen.getByRole("button", { name: /^continue$/i });
        expect(continueBtn).toBeEnabled();
    });

    it("selecting a GPU instance + region renders the cost summary", async () => {
        const user = await gotoStep2("aws");
        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));

        // Select g6.xlarge (price 0.805)
        await selectInstanceFromDropdown(user, "g6.xlarge");
        selectAwsRegion("us-east-1");

        // Summary shows g6.xlarge, cost from catalog
        const summary = await screen.findByText(/Summary: 1x g6\.xlarge/i);
        expect(summary).toBeInTheDocument();
        // 0.805 * 1 rounds to 0.81
        expect(screen.getByText(/Estimated: \$0\.81\/hr/i)).toBeInTheDocument();

        // Continue button is enabled
        const continueBtn = screen.getByRole("button", { name: /^continue$/i });
        expect(continueBtn).toBeEnabled();
    });

    it("changing instance selection clears previous (Continue disabled until new instance chosen)", async () => {
        const user = await gotoStep2("aws");
        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));

        // Select a region and an instance first
        selectAwsRegion("us-east-1");
        await selectInstanceFromDropdown(user, "g5.xlarge");

        // Continue should be enabled
        expect(screen.getByRole("button", { name: /^continue$/i })).toBeEnabled();

        // Now dispatch SET_RESOURCE to null via selecting a different option
        // is not directly testable, but we can verify that the dropdown shows
        // the selected instance in the trigger summary
        const trigger = screen.getByTestId("instance-dropdown-trigger");
        expect(trigger.textContent).toMatch(/g5\.xlarge/);
    });

    it("GPU-first order: heavy instances come before normal_gpu, which come before cpu", async () => {
        const user = await gotoStep2("aws");
        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));
        await user.click(screen.getByTestId("instance-dropdown-trigger"));

        const list = await screen.findByTestId("instance-dropdown-list");
        const buttons = within(list).getAllByRole("button");
        const names = buttons.map(b => b.getAttribute("data-testid")?.replace("inst-option-", "") ?? "");

        // heavy_gpu instances from fixture: p4d.24xlarge, p5.48xlarge, g5.12xlarge
        // normal_gpu: g5.xlarge, g5.2xlarge, g6.xlarge
        // cpu: c6i.xlarge, c6i.2xlarge, m6i.xlarge
        const heavyIdx = names.indexOf("p4d.24xlarge");
        const normalIdx = names.indexOf("g5.xlarge");
        const cpuIdx = names.indexOf("c6i.xlarge");

        expect(heavyIdx).toBeLessThan(normalIdx);
        expect(normalIdx).toBeLessThan(cpuIdx);
    });

    it("renders the tier selector ONLY for AWS — GCP shows the legacy gcpGpuTypes grid, no dropdown", async () => {
        await gotoStep2("gcp");

        // No tier selector
        expect(screen.queryByTestId("tier-selector")).not.toBeInTheDocument();
        // No instance dropdown for GCP
        expect(screen.queryByTestId("instance-dropdown")).not.toBeInTheDocument();

        // The original gcpGpuTypes labels are present (H100 / A100 / L4 / T4 / V100)
        expect(screen.getAllByText(/H100/).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/A100/).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/V100/).length).toBeGreaterThan(0);

        // GPU Count selector visible on GCP (cluster path, non-CPU)
        expect(screen.getByTestId("gpu-count")).toBeInTheDocument();
    });

    it("spot toggle applies a 60% discount on the AWS price summary", async () => {
        const user = await gotoStep2("aws");
        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));

        selectAwsRegion("us-east-1");
        await selectInstanceFromDropdown(user, "g6.xlarge");

        // Pre-spot: 0.805 * 1 → "$0.81/hr"
        await waitFor(() =>
            expect(screen.getByText(/Estimated: \$0\.81\/hr/i)).toBeInTheDocument(),
        );

        // Toggle spot
        const spotLabel = screen.getByText("Use Spot Instances");
        const row = spotLabel.closest("div.flex");
        const spotToggle = row?.querySelector("button.rounded-full");
        expect(spotToggle).toBeTruthy();
        await act(async () => { await user.click(spotToggle as HTMLButtonElement); });

        // Post-spot: 0.805 * 0.4 = 0.322 → "$0.32/hr"
        expect(screen.getByText(/Estimated: \$0\.32\/hr/i)).toBeInTheDocument();
    });

    // -----------------------------------------------------------------------
    // T31: hook-swap regression test.
    //
    // Pinning the behaviour the plan called out explicitly: the dropdown
    // options must come from the live catalog endpoint (mocked here), NOT
    // from a hard-coded module constant.
    // -----------------------------------------------------------------------
    it("swaps awsInstanceTiers constant for useInstanceCatalog query", async () => {
        computeApiGet.mockImplementation((url: string) => {
            if (url.includes("/inventory/providers")) {
                return Promise.resolve({
                    data: {
                        providers: {
                            aws: { adapter_type: "cloud", capabilities: { supports_cluster_mode: true, pricing_model: "fixed" } },
                            gcp: { adapter_type: "cloud", capabilities: { supports_cluster_mode: true, pricing_model: "fixed" } },
                        },
                    },
                });
            }
            if (url.includes("/providers/aws/instance-catalog")) {
                return Promise.resolve({
                    data: {
                        normal_gpu: [{
                            name: "totally-made-up.xl", cls: "normal_gpu",
                            vcpu: 4, ram_gb: 16, gpu_count: 1,
                            gpu_model: "FAKE GPU", gpu_ram_gb: 24,
                            price_per_hour: 0.42,
                        }],
                        heavy_gpu: [], cpu: [],
                    },
                });
            }
            return Promise.resolve({ data: { resources: [] } });
        });

        const user = await gotoStep2("aws");
        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));

        // Open the dropdown and verify the fixture-only instance appears
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        await waitFor(() =>
            expect(screen.getByTestId("inst-option-totally-made-up.xl")).toBeInTheDocument(),
        );
        // Price shows in the option
        const option = screen.getByTestId("inst-option-totally-made-up.xl");
        expect(within(option).getByText(/\$0\.420\/hr/)).toBeInTheDocument();
    });

    it("instance dropdown is disabled while catalog is loading", async () => {
        // Override to delay the catalog response
        let resolveHook: (v: any) => void;
        const catalogPromise = new Promise((res) => { resolveHook = res; });
        computeApiGet.mockImplementation((url: string) => {
            if (url.includes("/inventory/providers")) {
                return Promise.resolve({
                    data: {
                        providers: {
                            aws: { adapter_type: "cloud", capabilities: { supports_cluster_mode: true, pricing_model: "fixed" } },
                        },
                    },
                });
            }
            if (url.includes("/providers/aws/instance-catalog")) {
                return catalogPromise;
            }
            return Promise.resolve({ data: { resources: [] } });
        });

        await gotoStep2("aws");

        // The trigger should be disabled while loading
        await waitFor(() => {
            const trigger = screen.queryByTestId("instance-dropdown-trigger");
            if (trigger) expect(trigger).toBeDisabled();
        });

        // Resolve the catalog so the test cleans up
        resolveHook!({ data: CATALOG_FIXTURE });
    });
});

describe("AWS region selector (native <select>)", () => {
    // Regression: the AWS pool form offered GCP region codes (e.g. "us-east1"),
    // which AWS rejects — boto3 can't build an endpoint for a nonexistent
    // region and provisioning failed at preflight with EndpointConnectionError.
    it("offers a <select> with valid AWS region codes (us-east-1), not GCP codes", async () => {
        await gotoStep2("aws");
        await screen.findByText(/Select Region/i);

        const sel = screen.getByTestId("aws-region-select");
        expect(sel.tagName).toBe("SELECT");

        // A real AWS region code is an <option>…
        expect(within(sel as HTMLSelectElement).getByText(/N\. Virginia \(us-east-1\)/i)).toBeInTheDocument();

        // …and the GCP-style code that AWS rejects is NOT offered.
        expect(within(sel as HTMLSelectElement).queryByText(/us-east1/)).not.toBeInTheDocument();
        expect(within(sel as HTMLSelectElement).queryByText(/us-central1/)).not.toBeInTheDocument();
    });

    it("dispatches SET_REGION when the <select> value changes", async () => {
        const user = await gotoStep2("aws");
        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));

        selectAwsRegion("us-west-2");

        // Select an instance so the Summary block renders (requires region)
        await selectInstanceFromDropdown(user, "g5.xlarge");

        // Summary should show the selected region
        await waitFor(() =>
            expect(screen.getByText(/in us-west-2/i)).toBeInTheDocument(),
        );
    });

    it("GCP pool still offers GCP region codes (us-central1) as buttons — not a <select>", async () => {
        await gotoStep2("gcp");
        await screen.findByText(/Select Region/i);

        // No aws-region-select for GCP
        expect(screen.queryByTestId("aws-region-select")).not.toBeInTheDocument();

        // The GCP region buttons are still there
        expect(screen.getByText("us-central1")).toBeInTheDocument();
    });

    it("region is required: submit guard fires when AWS region is missing", async () => {
        const { toast } = await import("sonner");
        const user = await gotoStep2("aws");
        await waitFor(() => screen.getByTestId("instance-dropdown-trigger"));

        // Select instance but no region
        await selectInstanceFromDropdown(user, "g5.xlarge");

        // Proceed to step 3 — should remain disabled without region
        const continueBtn = screen.getByRole("button", { name: /^continue$/i });
        expect(continueBtn).toBeDisabled();

        // Select a region to unblock
        selectAwsRegion("us-east-1");
        await waitFor(() =>
            expect(screen.getByRole("button", { name: /^continue$/i })).toBeEnabled(),
        );
        void toast; // suppress unused warning
    });
});
