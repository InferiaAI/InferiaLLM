/**
 * Tests for the AWS instance-tier selector + useInstanceCatalog wiring.
 *
 * Scope:
 *   - The wizard fetches the catalog from useInstanceCatalog (T22) instead
 *     of importing a hard-coded module constant (T31 swap).
 *   - Default tier is "normal_gpu" with single-GPU AWS instances on screen.
 *   - "heavy_gpu" tier reveals multi-GPU and high-end instances + hides
 *     single-GPU ones.
 *   - "cpu" tier reveals c6i/m6i and hides every GPU instance + the GPU
 *     Count selector.
 *   - Selecting a CPU instance + region renders the cost summary using
 *     the catalog's price_per_hour (not the GCP fallback).
 *   - Changing tier clears any previously selected resource (Continue
 *     re-disabled).
 *   - The selector + catalog-grid render ONLY for AWS — GCP shows the
 *     legacy gcpGpuTypes table.
 *
 * Strategy:
 *   - Mount NewPool with auth + react-query providers stubbed.
 *   - Mock global.fetch to answer /api/v1/providers/aws/instance-catalog
 *     with a deterministic fixture.
 *   - Mock the compute API to advertise aws + gcp as registered providers
 *     so Step 1 → click → Step 2 lands on the cluster-provider branch.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
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

describe("AWS Instance Tier selector (useInstanceCatalog)", () => {
    it("defaults to 'normal_gpu' tier — shows the catalog's single-GPU rows, no heavy/cpu rows", async () => {
        await gotoStep2("aws");

        // Tier selector visible
        expect(screen.getByTestId("tier-selector")).toBeInTheDocument();

        // Normal-tier instances from the fixture must appear (waitFor: fetch
        // resolves async after the cluster block renders).
        await waitFor(() =>
            expect(screen.getByTestId("aws-inst-g5.xlarge")).toBeInTheDocument(),
        );
        expect(screen.getByTestId("aws-inst-g5.2xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-g6.xlarge")).toBeInTheDocument();

        // Heavy + CPU tier instances must NOT be on screen
        expect(screen.queryByTestId("aws-inst-p4d.24xlarge")).not.toBeInTheDocument();
        expect(screen.queryByTestId("aws-inst-p5.48xlarge")).not.toBeInTheDocument();
        expect(screen.queryByTestId("aws-inst-c6i.xlarge")).not.toBeInTheDocument();
        expect(screen.queryByTestId("aws-inst-m6i.xlarge")).not.toBeInTheDocument();

        // Default tier hint is the "normal" copy
        expect(screen.getByText(/Single-GPU instances for routine inference/i)).toBeInTheDocument();
    });

    it("switching to 'heavy_gpu' shows multi-GPU + high-end and hides single-GPU rows", async () => {
        const user = await gotoStep2("aws");
        // Wait for catalog so the tier-button click on heavy can render the
        // heavy_gpu rows immediately.
        await screen.findByTestId("aws-inst-g5.xlarge");

        await user.click(screen.getByTestId("tier-btn-heavy_gpu"));

        expect(screen.getByTestId("aws-inst-p4d.24xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-p5.48xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-g5.12xlarge")).toBeInTheDocument();

        // Normal-tier should be gone (g5.xlarge is normal_gpu in the
        // fixture; g5.12xlarge stays because that one is heavy_gpu)
        expect(screen.queryByTestId("aws-inst-g5.xlarge")).not.toBeInTheDocument();
        expect(screen.queryByTestId("aws-inst-g6.xlarge")).not.toBeInTheDocument();

        // Tier hint updates
        expect(screen.getByText(/Multi-GPU and high-end/i)).toBeInTheDocument();
        // GPU Count selector remains visible on heavy tier
        expect(screen.getByTestId("gpu-count")).toBeInTheDocument();
    });

    it("switching to 'cpu' shows c6i.* / m6i.*, hides every GPU instance, hides the GPU Count selector", async () => {
        const user = await gotoStep2("aws");
        await screen.findByTestId("aws-inst-g5.xlarge");

        await user.click(screen.getByTestId("tier-btn-cpu"));

        // CPU instances on screen
        expect(screen.getByTestId("aws-inst-c6i.xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-c6i.2xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-m6i.xlarge")).toBeInTheDocument();

        // Every GPU instance must be hidden
        for (const id of [
            "g5.xlarge", "g5.2xlarge", "g6.xlarge",
            "p4d.24xlarge", "p5.48xlarge", "g5.12xlarge",
        ]) {
            expect(screen.queryByTestId(`aws-inst-${id}`)).not.toBeInTheDocument();
        }

        // GPU Count selector must be hidden
        expect(screen.queryByTestId("gpu-count")).not.toBeInTheDocument();

        // Label switches to "Instance Type"
        expect(screen.getByText("Select Instance Type")).toBeInTheDocument();

        // CPU tier hint is the no-GPU copy
        expect(screen.getByText(/No-GPU compute/i)).toBeInTheDocument();
    });

    it("selecting a CPU instance and a region renders the cost summary without crashing", async () => {
        const user = await gotoStep2("aws");
        await screen.findByTestId("aws-inst-g5.xlarge");

        await user.click(screen.getByTestId("tier-btn-cpu"));
        // Pick the cheapest CPU instance (c6i.xlarge, 0.170)
        await user.click(screen.getByTestId("aws-inst-c6i.xlarge"));
        // Pick a region (any will do — the summary needs both).
        // "N. Virginia" is the first entry in the static awsRegions list.
        await user.click(screen.getByRole("button", { name: /N\. Virginia/i }));

        // Summary block uses the instance type (not the gpu_type "(none)")
        const summary = await screen.findByText(/Summary: 1x c6i\.xlarge/i);
        expect(summary).toBeInTheDocument();
        // The estimated cost line uses the catalog's price_per_hour
        // (0.170 * 1 GPU rounds to 0.17). Verify it's NOT the GCP
        // fallback (which would be 1.0 for an unknown gpu_type).
        expect(screen.getByText(/Estimated: \$0\.17\/hr/i)).toBeInTheDocument();

        // Continue button is enabled
        const continueBtn = screen.getByRole("button", { name: /^continue$/i });
        expect(continueBtn).toBeEnabled();
    });

    it("changing tier clears the previously selected resource (Continue disabled again)", async () => {
        const user = await gotoStep2("aws");
        await screen.findByTestId("aws-inst-g5.xlarge");

        // Select region + a Normal-tier instance first.
        // "N. Virginia" is the first entry in the static awsRegions list.
        await user.click(screen.getByRole("button", { name: /N\. Virginia/i }));
        await user.click(screen.getByTestId("aws-inst-g5.xlarge"));

        // Continue should now be enabled.
        const continueBtn = screen.getByRole("button", { name: /^continue$/i });
        expect(continueBtn).toBeEnabled();

        // Switch tier → selectedResource should reset → Continue disabled.
        await user.click(screen.getByTestId("tier-btn-heavy_gpu"));
        expect(screen.getByRole("button", { name: /^continue$/i })).toBeDisabled();

        // The cost summary should also disappear because selectedResource is null.
        expect(screen.queryByText(/^Summary: /i)).not.toBeInTheDocument();
    });

    it("switching from heavy with gpuCount=4 to CPU forces gpuCount=1 and hides the count", async () => {
        const user = await gotoStep2("aws");
        await screen.findByTestId("aws-inst-g5.xlarge");

        // Heavy + pick 4 GPUs
        await user.click(screen.getByTestId("tier-btn-heavy_gpu"));
        const gpuCountBlock = screen.getByTestId("gpu-count");
        await user.click(within(gpuCountBlock).getByRole("button", { name: "4x" }));

        // Switching to CPU removes the count selector — and on switching back to
        // a GPU tier, gpuCount has been reset to 1 (the "force-reset" reducer
        // branch). Default "1x" button should be the selected one.
        await user.click(screen.getByTestId("tier-btn-cpu"));
        expect(screen.queryByTestId("gpu-count")).not.toBeInTheDocument();

        await user.click(screen.getByTestId("tier-btn-normal_gpu"));
        const newCountBlock = screen.getByTestId("gpu-count");
        // The "1x" button should be the active (border-ember-600) one.
        const oneX = within(newCountBlock).getByRole("button", { name: "1x" });
        expect(oneX.className).toMatch(/border-ember-600/);
    });

    it("renders the tier selector ONLY for AWS — GCP shows the legacy gcpGpuTypes grid", async () => {
        await gotoStep2("gcp");

        // No tier selector
        expect(screen.queryByTestId("tier-selector")).not.toBeInTheDocument();
        // No data-testid for instance buttons (those are AWS-only)
        expect(screen.queryByTestId("aws-inst-g5.xlarge")).not.toBeInTheDocument();

        // The original gcpGpuTypes labels are present (H100 / A100 / L4 / T4 / V100)
        // — at least the H100 + A100 + V100 ones (visible in the GPU Type grid).
        // We use getAllByText because the same name can appear in the Summary if
        // selected; we just need at least one.
        expect(screen.getAllByText(/H100/).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/A100/).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/V100/).length).toBeGreaterThan(0);

        // GPU Count selector visible on GCP (cluster path, non-CPU)
        expect(screen.getByTestId("gpu-count")).toBeInTheDocument();
    });

    it("tier buttons get the ember-selected style when active", async () => {
        const user = await gotoStep2("aws");
        await screen.findByTestId("aws-inst-g5.xlarge");

        // Initially "Normal GPU" is selected
        expect(screen.getByTestId("tier-btn-normal_gpu").className).toMatch(/border-ember-600/);
        expect(screen.getByTestId("tier-btn-heavy_gpu").className).not.toMatch(/border-ember-600/);

        await user.click(screen.getByTestId("tier-btn-heavy_gpu"));
        expect(screen.getByTestId("tier-btn-heavy_gpu").className).toMatch(/border-ember-600/);
        expect(screen.getByTestId("tier-btn-normal_gpu").className).not.toMatch(/border-ember-600/);
    });

    it("AWS instance cards show the price_per_hour value prefixed with ≈", async () => {
        await gotoStep2("aws");

        const card = await screen.findByTestId("aws-inst-g6.xlarge");
        // g6.xlarge price in the fixture is 0.805.
        expect(within(card).getByText(/≈\$0\.805\/hr/)).toBeInTheDocument();
        // And the gpu model + VRAM from the catalog row shows up too
        expect(within(card).getByText(/NVIDIA L4 • 24GB VRAM/)).toBeInTheDocument();
    });

    it("spot toggle applies a 60% discount on the AWS price summary", async () => {
        const user = await gotoStep2("aws");
        await screen.findByTestId("aws-inst-g6.xlarge");

        // "N. Virginia" is the first entry in the static awsRegions list.
        await user.click(screen.getByRole("button", { name: /N\. Virginia/i }));
        await user.click(screen.getByTestId("aws-inst-g6.xlarge"));

        // Pre-spot: 0.805 * 1 → "$0.81/hr" when rounded to two decimals
        expect(screen.getByText(/Estimated: \$0\.81\/hr/i)).toBeInTheDocument();

        // Toggle spot (the only switch in the AWS-config block).
        // The toggle is a sibling <button> inside the same flex row as the
        // "Use Spot Instances" label. Walk up to the row container and pick
        // the button that has the `rounded-full` classes we use for switches.
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
    // from a hard-coded module constant. If anyone ever re-introduces an
    // inline awsInstanceTiers constant, this test still passes — but its
    // sibling case below ("fixture-only instance is rendered") will fail
    // unless the rendering really did consume the fetch response.
    // -----------------------------------------------------------------------
    it("swaps awsInstanceTiers constant for useInstanceCatalog query", async () => {
        // Override the catalog fetch with a payload that contains ONLY an
        // exotic name never present in any previous hard-coded constant.
        // If the rendering really comes from the API, this name will be
        // visible; if it falls back to a stale local constant, the test
        // will fail.
        // Override computeApiGet for this test only — the catalog hook uses
        // computeApi.get (axios), not global.fetch.
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

        await gotoStep2("aws");
        await waitFor(() =>
            expect(screen.getByText("totally-made-up.xl")).toBeInTheDocument(),
        );
        // The catalog row's price comes through too.
        expect(screen.getByText(/≈\$0\.420\/hr/)).toBeInTheDocument();
    });
});

describe("AWS region selector", () => {
    // Regression: the AWS pool form offered GCP region codes (e.g. "us-east1"),
    // which AWS rejects — boto3 can't build an endpoint for a nonexistent
    // region and provisioning failed at preflight with EndpointConnectionError.
    it("offers valid AWS region codes (us-east-1), not GCP codes (us-east1)", async () => {
        await gotoStep2("aws");
        await screen.findByText(/Select Region/i);
        // A real AWS region code is presented…
        expect(screen.getByText("us-east-1")).toBeInTheDocument();
        // …and the GCP-style code that AWS rejects is NOT offered.
        expect(screen.queryByText("us-east1")).not.toBeInTheDocument();
        expect(screen.queryByText("us-central1")).not.toBeInTheDocument();
    });

    it("GCP pool still offers GCP region codes (us-central1)", async () => {
        await gotoStep2("gcp");
        await screen.findByText(/Select Region/i);
        expect(screen.getByText("us-central1")).toBeInTheDocument();
    });
});
