import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ProvisioningStatus from "./ProvisioningStatus";
import { ALL_PHASES, type ProvisioningSummary } from "@/services/provisioningService";

const baseSummary: ProvisioningSummary = {
  current_phase: "pulumi_up",
  terminal: false,
  phases: [
    { phase: "prepare", status: "succeeded",
      started_at: "2026-05-25T00:00:00Z", ended_at: "2026-05-25T00:00:02Z",
      last_message: null },
    { phase: "pulumi_up", status: "running",
      started_at: "2026-05-25T00:00:03Z", ended_at: null,
      last_message: "creating ec2" },
  ],
};

describe("ProvisioningStatus", () => {
  it("renders all 8 phases in order", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const phases = screen.getAllByTestId(/^phase-row-/);
    expect(phases).toHaveLength(ALL_PHASES.length);
    ALL_PHASES.forEach((p, i) => {
      expect(phases[i]).toHaveAttribute("data-testid", `phase-row-${p}`);
    });
  });

  it("running phase shows spinner icon", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const row = screen.getByTestId("phase-row-pulumi_up");
    expect(row.querySelector('[data-icon="spinner"]')).not.toBeNull();
  });

  it("succeeded phase shows check icon", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const row = screen.getByTestId("phase-row-prepare");
    expect(row.querySelector('[data-icon="check"]')).not.toBeNull();
  });

  it("pending phase shows dim circle icon", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const row = screen.getByTestId("phase-row-ready");
    expect(row.querySelector('[data-icon="pending"]')).not.toBeNull();
  });

  it("failed phase shows error icon and red banner", () => {
    const failed: ProvisioningSummary = {
      current_phase: null, terminal: true,
      phases: [
        { phase: "pulumi_up", status: "failed",
          started_at: "2026-05-25T00:00:00Z", ended_at: "2026-05-25T00:00:10Z",
          last_message: "insufficient capacity" },
      ],
    };
    render(<ProvisioningStatus summary={failed} />);
    const row = screen.getByTestId("phase-row-pulumi_up");
    expect(row.querySelector('[data-icon="error"]')).not.toBeNull();
    expect(screen.getByText(/insufficient capacity/)).toBeInTheDocument();
  });

  it("displays the running phase's last_message", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    expect(screen.getByText("creating ec2")).toBeInTheDocument();
  });
});
