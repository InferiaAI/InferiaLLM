/**
 * Tests for the AWS instance-tier selector added to NewPool.tsx.
 *
 * Scope: behavior the selector adds on top of the existing wizard.
 *   - default tier is "normal" with the 4 single-GPU AWS instances
 *   - "heavy" tier reveals p4d/p5/p5e and hides g4dn/g5
 *   - "cpu" tier reveals t3.* and hides GPU instances + GPU Count selector
 *   - selecting a CPU instance + clicking Continue does not crash the cost summary
 *   - changing tier clears any previously selected resource (Continue re-disabled)
 *   - the selector only renders for AWS — GCP shows the legacy gcpGpuTypes grid
 *
 * Strategy: mount NewPool with auth + react-query providers stubbed, and an
 * /inventory/providers response that ONLY includes the two cluster providers
 * we care about (aws + gcp). Step 1 → click provider card → Step 2 renders
 * the cluster-provider branch which is where the tier selector lives.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

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
    },
}));

vi.mock("@/services/nodeService", () => ({
    addWorkerNode: vi.fn(),
    listNodes: vi.fn().mockResolvedValue([]),
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
        // /deployment/provider/resources is only consumed by the non-cluster
        // path, but the effect calls it for AWS too; return an empty list so
        // the loading spinner can resolve.
        return Promise.resolve({ data: { resources: [] } });
    });
});

afterEach(() => {
    vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AWS Instance Tier selector", () => {
    it("defaults to 'normal' tier — shows 4 single-GPU instances, no p4d/p5/t3", async () => {
        await gotoStep2("aws");

        // Tier selector visible
        expect(screen.getByTestId("tier-selector")).toBeInTheDocument();

        // Normal-tier instances present
        expect(screen.getByTestId("aws-inst-g4dn.xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-g4dn.2xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-g5.xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-g6.xlarge")).toBeInTheDocument();

        // Heavy + CPU tier instances must NOT be on screen
        expect(screen.queryByTestId("aws-inst-p4d.24xlarge")).not.toBeInTheDocument();
        expect(screen.queryByTestId("aws-inst-p5.48xlarge")).not.toBeInTheDocument();
        expect(screen.queryByTestId("aws-inst-p5e.48xlarge")).not.toBeInTheDocument();
        expect(screen.queryByTestId("aws-inst-t3.small")).not.toBeInTheDocument();

        // Default tier hint is the "normal" copy
        expect(screen.getByText(/Single-GPU instances for routine inference/i)).toBeInTheDocument();
    });

    it("switching to 'heavy' shows p4d/p5/p5e and hides g4dn/g5", async () => {
        const user = await gotoStep2("aws");

        await user.click(screen.getByTestId("tier-btn-heavy"));

        expect(screen.getByTestId("aws-inst-p4d.24xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-p4de.24xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-p5.48xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-p5e.48xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-g6e.xlarge")).toBeInTheDocument();

        // Normal-tier should be gone
        expect(screen.queryByTestId("aws-inst-g4dn.xlarge")).not.toBeInTheDocument();
        expect(screen.queryByTestId("aws-inst-g5.xlarge")).not.toBeInTheDocument();

        // Tier hint updates
        expect(screen.getByText(/Multi-GPU and high-end/i)).toBeInTheDocument();
        // GPU Count selector remains visible on heavy tier
        expect(screen.getByTestId("gpu-count")).toBeInTheDocument();
    });

    it("switching to 'cpu' shows t3.* / m5.* / c5.*, hides every GPU instance, hides the GPU Count selector", async () => {
        const user = await gotoStep2("aws");

        await user.click(screen.getByTestId("tier-btn-cpu"));

        // CPU instances on screen
        expect(screen.getByTestId("aws-inst-t3.small")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-t3.medium")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-t3.large")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-t3.xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-c5.large")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-c5.xlarge")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-m5.large")).toBeInTheDocument();
        expect(screen.getByTestId("aws-inst-m5.xlarge")).toBeInTheDocument();

        // Every GPU instance must be hidden
        for (const id of [
            "g4dn.xlarge", "g4dn.2xlarge", "g5.xlarge", "g6.xlarge",
            "p3.2xlarge", "g6e.xlarge", "p4d.24xlarge", "p4de.24xlarge",
            "p5.48xlarge", "p5e.48xlarge",
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

        await user.click(screen.getByTestId("tier-btn-cpu"));
        // Pick the cheapest CPU instance
        await user.click(screen.getByTestId("aws-inst-t3.small"));
        // Pick a region (any will do — the summary needs both)
        await user.click(screen.getByRole("button", { name: /Iowa/i }));

        // Summary block uses the instance type (not the gpu_type "(none)")
        const summary = await screen.findByText(/Summary: 1x t3\.small/i);
        expect(summary).toBeInTheDocument();
        // And the estimated cost is the price_per_hour from awsInstanceTiers
        // (0.02 * 1 GPU = 0.02). Verify the cost line uses that, not the
        // GCP fallback (which would be 1.0 for unknown gpu_type "(none)").
        expect(screen.getByText(/Estimated: \$0\.02\/hr/i)).toBeInTheDocument();

        // Continue button is enabled
        const continueBtn = screen.getByRole("button", { name: /^continue$/i });
        expect(continueBtn).toBeEnabled();
    });

    it("changing tier clears the previously selected resource (Continue disabled again)", async () => {
        const user = await gotoStep2("aws");

        // Select region + a Normal-tier instance first.
        await user.click(screen.getByRole("button", { name: /Iowa/i }));
        await user.click(screen.getByTestId("aws-inst-g4dn.xlarge"));

        // Continue should now be enabled.
        const continueBtn = screen.getByRole("button", { name: /^continue$/i });
        expect(continueBtn).toBeEnabled();

        // Switch tier → selectedResource should reset → Continue disabled.
        await user.click(screen.getByTestId("tier-btn-heavy"));
        expect(screen.getByRole("button", { name: /^continue$/i })).toBeDisabled();

        // The cost summary should also disappear because selectedResource is null.
        expect(screen.queryByText(/^Summary: /i)).not.toBeInTheDocument();
    });

    it("switching from heavy with gpuCount=4 to CPU forces gpuCount=1 and hides the count", async () => {
        const user = await gotoStep2("aws");

        // Heavy + pick 4 GPUs
        await user.click(screen.getByTestId("tier-btn-heavy"));
        const gpuCountBlock = screen.getByTestId("gpu-count");
        await user.click(within(gpuCountBlock).getByRole("button", { name: "4x" }));

        // Switching to CPU removes the count selector — and on switching back to
        // a GPU tier, gpuCount has been reset to 1 (the "force-reset" reducer
        // branch). Default "1x" button should be the selected one.
        await user.click(screen.getByTestId("tier-btn-cpu"));
        expect(screen.queryByTestId("gpu-count")).not.toBeInTheDocument();

        await user.click(screen.getByTestId("tier-btn-normal"));
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
        expect(screen.queryByTestId("aws-inst-g4dn.xlarge")).not.toBeInTheDocument();

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

        // Initially "Normal GPU" is selected
        expect(screen.getByTestId("tier-btn-normal").className).toMatch(/border-ember-600/);
        expect(screen.getByTestId("tier-btn-heavy").className).not.toMatch(/border-ember-600/);

        await user.click(screen.getByTestId("tier-btn-heavy"));
        expect(screen.getByTestId("tier-btn-heavy").className).toMatch(/border-ember-600/);
        expect(screen.getByTestId("tier-btn-normal").className).not.toMatch(/border-ember-600/);
    });

    it("AWS instance cards always show the est_per_hour price prefixed with ≈", async () => {
        await gotoStep2("aws");

        const card = screen.getByTestId("aws-inst-g4dn.xlarge");
        expect(within(card).getByText(/≈\$0\.520\/hr/)).toBeInTheDocument();

        // And the description from awsInstanceTiers shows up too
        expect(within(card).getByText(/NVIDIA T4 — entry-level GPU/)).toBeInTheDocument();
    });

    it("spot toggle applies a 60% discount on the AWS price summary", async () => {
        const user = await gotoStep2("aws");

        await user.click(screen.getByRole("button", { name: /Iowa/i }));
        await user.click(screen.getByTestId("aws-inst-g4dn.xlarge"));

        // Pre-spot: 0.52 * 1
        expect(screen.getByText(/Estimated: \$0\.52\/hr/i)).toBeInTheDocument();

        // Toggle spot (the only switch in the AWS-config block).
        // The toggle is a sibling <button> inside the same flex row as the
        // "Use Spot Instances" label. Walk up to the row container and pick
        // the button that has the `rounded-full` classes we use for switches.
        const spotLabel = screen.getByText("Use Spot Instances");
        const row = spotLabel.closest("div.flex");
        const spotToggle = row?.querySelector("button.rounded-full");
        expect(spotToggle).toBeTruthy();
        await act(async () => { await user.click(spotToggle as HTMLButtonElement); });

        // Post-spot: 0.52 * 0.4 = 0.208 -> 0.21 when rounded
        expect(screen.getByText(/Estimated: \$0\.21\/hr/i)).toBeInTheDocument();
    });
});
