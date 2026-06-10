/**
 * Unit tests for InstanceDropdown component.
 *
 * Covers:
 *   - GPU-first ordering (heavy before normal before cpu) when rendered
 *   - priceLabel: numeric price vs "price N/A" for null
 *   - Selection callback fires with the correct InstanceType object
 *   - Opens on trigger click, closes on item select
 *   - Closes on Escape keydown
 *   - Closes on outside click
 *   - Shows "Select an instance type" placeholder when value is null
 *   - Shows selected instance summary when value is set
 *   - Empty list shows "No instance types available"
 *   - Trigger is disabled when loading=true
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InstanceDropdown } from "./InstanceDropdown";
import type { InstanceType } from "@/hooks/useInstanceCatalog";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const GPU_INSTANCE: InstanceType = {
    name: "g6.xlarge",
    cls: "normal_gpu",
    vcpu: 4,
    ram_gb: 16,
    gpu_count: 1,
    gpu_model: "NVIDIA L4",
    gpu_ram_gb: 24,
    price_per_hour: 0.805,
};

const HEAVY_GPU_INSTANCE: InstanceType = {
    name: "p4d.24xlarge",
    cls: "heavy_gpu",
    vcpu: 96,
    ram_gb: 1152,
    gpu_count: 8,
    gpu_model: "NVIDIA A100",
    gpu_ram_gb: 320,
    price_per_hour: 32.770,
};

const CPU_INSTANCE: InstanceType = {
    name: "c6i.xlarge",
    cls: "cpu",
    vcpu: 4,
    ram_gb: 8,
    gpu_count: 0,
    gpu_model: null,
    gpu_ram_gb: 0,
    price_per_hour: 0.170,
};

const NULL_PRICE_INSTANCE: InstanceType = {
    name: "g5.xlarge",
    cls: "normal_gpu",
    vcpu: 4,
    ram_gb: 16,
    gpu_count: 1,
    gpu_model: "NVIDIA A10G",
    gpu_ram_gb: 24,
    price_per_hour: null,
};

// GPU-first ordered list (as NewPool would pass it)
const ALL_INSTANCES: InstanceType[] = [
    HEAVY_GPU_INSTANCE,
    GPU_INSTANCE,
    NULL_PRICE_INSTANCE,
    CPU_INSTANCE,
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

afterEach(() => {
    vi.clearAllMocks();
});

function renderDropdown(
    overrides: Partial<React.ComponentProps<typeof InstanceDropdown>> = {},
) {
    const onSelect = vi.fn();
    const props = {
        instances: ALL_INSTANCES,
        value: null,
        onSelect,
        loading: false,
        ...overrides,
    };
    render(<InstanceDropdown {...props} />);
    return { onSelect };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("InstanceDropdown — rendering", () => {
    it("shows placeholder text when value is null", () => {
        renderDropdown({ value: null });
        expect(screen.getByTestId("instance-dropdown-trigger")).toHaveTextContent(
            /Select an instance type/,
        );
    });

    it("shows selected instance summary in trigger when value is set (GPU instance)", () => {
        renderDropdown({ value: "g6.xlarge" });
        const trigger = screen.getByTestId("instance-dropdown-trigger");
        // summary() for GPU: "g6.xlarge — NVIDIA L4 24GB · 1 GPU · $0.805/hr"
        expect(trigger.textContent).toMatch(/g6\.xlarge/);
        expect(trigger.textContent).toMatch(/NVIDIA L4/);
        expect(trigger.textContent).toMatch(/\$0\.805\/hr/);
    });

    it("shows selected instance summary for CPU instance (vcpu/ram line, no GPU)", () => {
        renderDropdown({ value: "c6i.xlarge" });
        const trigger = screen.getByTestId("instance-dropdown-trigger");
        // summary() for CPU: "c6i.xlarge — 4 vCPU · 8GB · $0.170/hr"
        expect(trigger.textContent).toMatch(/c6i\.xlarge/);
        expect(trigger.textContent).toMatch(/vCPU/);
        expect(trigger.textContent).toMatch(/\$0\.170\/hr/);
    });

    it("shows 'Loading instance types…' and trigger is disabled when loading=true", () => {
        renderDropdown({ loading: true });
        const trigger = screen.getByTestId("instance-dropdown-trigger");
        expect(trigger).toBeDisabled();
        expect(trigger.textContent).toMatch(/Loading instance types/);
    });

    it("dropdown is closed by default", () => {
        renderDropdown();
        expect(screen.queryByTestId("instance-dropdown-list")).not.toBeInTheDocument();
    });
});

describe("InstanceDropdown — open/close behavior", () => {
    it("opens the list on trigger click", async () => {
        const user = userEvent.setup();
        renderDropdown();
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        expect(screen.getByTestId("instance-dropdown-list")).toBeInTheDocument();
    });

    it("closes the list when an item is selected", async () => {
        const user = userEvent.setup();
        renderDropdown();
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        expect(screen.getByTestId("instance-dropdown-list")).toBeInTheDocument();

        await user.click(screen.getByTestId("inst-option-g6.xlarge"));
        expect(screen.queryByTestId("instance-dropdown-list")).not.toBeInTheDocument();
    });

    it("closes on Escape keydown", async () => {
        const user = userEvent.setup();
        renderDropdown();
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        expect(screen.getByTestId("instance-dropdown-list")).toBeInTheDocument();

        await user.keyboard("{Escape}");
        await waitFor(() =>
            expect(screen.queryByTestId("instance-dropdown-list")).not.toBeInTheDocument(),
        );
    });

    it("closes on click outside", async () => {
        const user = userEvent.setup();
        renderDropdown();
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        expect(screen.getByTestId("instance-dropdown-list")).toBeInTheDocument();

        // Click outside the dropdown container
        fireEvent.mouseDown(document.body);
        await waitFor(() =>
            expect(screen.queryByTestId("instance-dropdown-list")).not.toBeInTheDocument(),
        );
    });
});

describe("InstanceDropdown — selection callback", () => {
    it("calls onSelect with the correct InstanceType object when an item is clicked", async () => {
        const user = userEvent.setup();
        const onSelect = vi.fn();
        render(<InstanceDropdown instances={ALL_INSTANCES} value={null} onSelect={onSelect} />);

        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        await user.click(screen.getByTestId("inst-option-g6.xlarge"));

        expect(onSelect).toHaveBeenCalledOnce();
        expect(onSelect).toHaveBeenCalledWith(GPU_INSTANCE);
    });

    it("calls onSelect with CPU instance when CPU option clicked", async () => {
        const user = userEvent.setup();
        const onSelect = vi.fn();
        render(<InstanceDropdown instances={ALL_INSTANCES} value={null} onSelect={onSelect} />);

        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        await user.click(screen.getByTestId("gpu-only-toggle")); // reveal CPU instances (GPU-only is on by default)
        await user.click(screen.getByTestId("inst-option-c6i.xlarge"));

        expect(onSelect).toHaveBeenCalledOnce();
        expect(onSelect).toHaveBeenCalledWith(CPU_INSTANCE);
    });
});

describe("InstanceDropdown — priceLabel (price vs null)", () => {
    it("shows formatted price for a numeric price_per_hour", async () => {
        const user = userEvent.setup();
        renderDropdown({ instances: [GPU_INSTANCE], value: null });
        await user.click(screen.getByTestId("instance-dropdown-trigger"));

        const option = screen.getByTestId("inst-option-g6.xlarge");
        // The price is shown as "$0.805/hr" in the option card
        expect(within(option).getByText(/\$0\.805\/hr/)).toBeInTheDocument();
    });

    it("shows 'price N/A' for null price_per_hour", async () => {
        const user = userEvent.setup();
        renderDropdown({ instances: [NULL_PRICE_INSTANCE], value: null });
        await user.click(screen.getByTestId("instance-dropdown-trigger"));

        const option = screen.getByTestId("inst-option-g5.xlarge");
        expect(within(option).getByText(/price N\/A/i)).toBeInTheDocument();
    });

    it("shows 'price N/A' in trigger summary for null price instance", () => {
        renderDropdown({ instances: [NULL_PRICE_INSTANCE], value: "g5.xlarge" });
        const trigger = screen.getByTestId("instance-dropdown-trigger");
        expect(trigger.textContent).toMatch(/price N\/A/i);
    });
});

describe("InstanceDropdown — GPU-only toggle + smallest-first ordering", () => {
    it("defaults to GPU only: CPU instances hidden until the toggle is turned off", async () => {
        const user = userEvent.setup();
        renderDropdown(); // ALL_INSTANCES includes c6i (cpu)
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        // GPU instances shown, CPU hidden by default
        expect(screen.getByTestId("inst-option-g6.xlarge")).toBeInTheDocument();
        expect(screen.queryByTestId("inst-option-c6i.xlarge")).not.toBeInTheDocument();
        // Toggle GPU-only OFF (toggle is inside the container, so the list stays open)
        await user.click(screen.getByTestId("gpu-only-toggle"));
        expect(screen.getByTestId("inst-option-c6i.xlarge")).toBeInTheDocument();
    });

    it("GPU-only toggle defaults to checked (on)", () => {
        renderDropdown();
        expect(screen.getByTestId("gpu-only-toggle")).toBeChecked();
    });

    it("orders smallest instance first: ascending vCPU → RAM → GPU (GPU-only view)", async () => {
        const user = userEvent.setup();
        renderDropdown(); // p4d(96 vcpu), g6(4), g5(4), c6i(4,cpu→hidden)
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        const list = screen.getByTestId("instance-dropdown-list");
        const names = within(list)
            .getAllByRole("button")
            .map((b) => b.getAttribute("data-testid")?.replace("inst-option-", "") ?? "");
        // GPU-only: g6(4) & g5(4) tie (same vcpu/ram/gpu) → stable input order; p4d(96) last.
        expect(names).toEqual(["g6.xlarge", "g5.xlarge", "p4d.24xlarge"]);
    });

    it("with GPU-only off, sorts smallest-first across GPU and CPU together", async () => {
        const user = userEvent.setup();
        renderDropdown();
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        await user.click(screen.getByTestId("gpu-only-toggle"));
        const list = screen.getByTestId("instance-dropdown-list");
        const names = within(list)
            .getAllByRole("button")
            .map((b) => b.getAttribute("data-testid")?.replace("inst-option-", "") ?? "");
        // vCPU asc: c6i(4 vcpu, 8GB) smallest of the vcpu=4 group; p4d(96) last.
        expect(names[0]).toBe("c6i.xlarge");
        expect(names[names.length - 1]).toBe("p4d.24xlarge");
    });

    it("GPU instance shows gpu_model, gpu_ram_gb, gpu_count details in option", async () => {
        const user = userEvent.setup();
        renderDropdown({ instances: [GPU_INSTANCE], value: null });
        await user.click(screen.getByTestId("instance-dropdown-trigger"));

        const option = screen.getByTestId("inst-option-g6.xlarge");
        expect(within(option).getByText(/NVIDIA L4/)).toBeInTheDocument();
        expect(within(option).getByText(/24GB VRAM/)).toBeInTheDocument();
        expect(within(option).getByText(/1 GPU/)).toBeInTheDocument();
    });

    it("CPU instance (GPU-only off) does NOT show a GPU details row in option", async () => {
        const user = userEvent.setup();
        renderDropdown({ instances: [CPU_INSTANCE], value: null });
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        await user.click(screen.getByTestId("gpu-only-toggle")); // reveal the CPU instance

        const option = screen.getByTestId("inst-option-c6i.xlarge");
        expect(within(option).queryByText(/VRAM/)).not.toBeInTheDocument();
        expect(within(option).getByText(/4 vCPU/)).toBeInTheDocument();
        expect(within(option).getByText(/8GB RAM/)).toBeInTheDocument();
    });
});

describe("InstanceDropdown — empty state", () => {
    it("shows 'No instance types available' when instances list is empty", async () => {
        const user = userEvent.setup();
        renderDropdown({ instances: [] });
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        expect(screen.getByTestId("instance-dropdown-list")).toBeInTheDocument();
        // GPU-only is on by default → "No GPU instance types available"; either empty message is acceptable.
        expect(screen.getByText(/No (GPU )?instance types available/)).toBeInTheDocument();
    });
});

describe("InstanceDropdown — selected item highlight", () => {
    it("currently selected item is marked data-selected=true, unselected items are data-selected=false", async () => {
        const user = userEvent.setup();
        renderDropdown({ instances: [GPU_INSTANCE, CPU_INSTANCE], value: "g6.xlarge" });
        await user.click(screen.getByTestId("instance-dropdown-trigger"));
        await user.click(screen.getByTestId("gpu-only-toggle")); // reveal the CPU instance too

        const selected = screen.getByTestId("inst-option-g6.xlarge");
        const unselected = screen.getByTestId("inst-option-c6i.xlarge");

        expect(selected).toHaveAttribute("data-selected", "true");
        expect(unselected).toHaveAttribute("data-selected", "false");

        // Also verify selected item has the bg-accent class specifically
        // (not just hover:bg-accent which all items have)
        // We check the class includes the standalone token "bg-accent" but not
        // via a simple regex (hover:bg-accent would also match). Instead, check
        // the full classList:
        expect(selected.classList.contains("bg-accent")).toBe(true);
        expect(unselected.classList.contains("bg-accent")).toBe(false);
    });
});
