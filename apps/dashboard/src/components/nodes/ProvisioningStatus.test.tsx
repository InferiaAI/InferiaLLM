import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import ProvisioningStatus from "./ProvisioningStatus";
import { ALL_PHASES, type ProvisioningSummary } from "@/services/provisioningService";

const baseSummary: ProvisioningSummary = {
  current_phase: "provisioning",
  terminal: false,
  phases: [
    { phase: "preflight", status: "succeeded",
      started_at: "2026-05-25T00:00:00Z", ended_at: "2026-05-25T00:00:02Z",
      last_message: null },
    { phase: "provisioning", status: "running",
      started_at: "2026-05-25T00:00:03Z", ended_at: null,
      last_message: "creating ec2" },
  ],
};

describe("ProvisioningStatus", () => {
  it("renders all phases in order", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const phases = screen.getAllByTestId(/^phase-row-/);
    expect(phases).toHaveLength(ALL_PHASES.length);
    ALL_PHASES.forEach((p, i) => {
      expect(phases[i]).toHaveAttribute("data-testid", `phase-row-${p}`);
    });
  });

  it("running phase shows spinner icon", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const row = screen.getByTestId("phase-row-provisioning");
    expect(row.querySelector('[data-icon="spinner"]')).not.toBeNull();
  });

  it("succeeded phase shows check icon", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const row = screen.getByTestId("phase-row-preflight");
    expect(row.querySelector('[data-icon="check"]')).not.toBeNull();
  });

  it("pending phase shows dim circle icon", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    const row = screen.getByTestId("phase-row-ready");
    expect(row.querySelector('[data-icon="pending"]')).not.toBeNull();
  });

  it("renders the Nosana phase skeleton (scheduling/loading/serving) for DePIN phases", () => {
    const nosana: ProvisioningSummary = {
      current_phase: "loading",
      terminal: false,
      phases: [
        { phase: "scheduling", status: "succeeded", started_at: null, ended_at: null, last_message: "Scheduled on a Nosana node" },
        { phase: "loading", status: "running", started_at: null, ended_at: null, last_message: "Pulling image & loading the model…" },
        { phase: "serving", status: "pending", started_at: null, ended_at: null, last_message: "Waiting for the endpoint to serve…" },
      ],
    };
    render(<ProvisioningStatus summary={nosana} />);
    const rows = screen.getAllByTestId(/^phase-row-/);
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "phase-row-scheduling", "phase-row-loading", "phase-row-serving",
    ]);
    // no AWS phases leak in
    expect(screen.queryByTestId("phase-row-preflight")).toBeNull();
    expect(screen.queryByTestId("phase-row-bootstrapping")).toBeNull();
  });

  it("derives the ready phase as succeeded when terminal without failure", () => {
    const done: ProvisioningSummary = {
      current_phase: "ready",
      terminal: true,
      phases: [
        { phase: "preflight", status: "succeeded",
          started_at: "2026-05-25T00:00:00Z", ended_at: "2026-05-25T00:00:02Z",
          last_message: null },
        { phase: "provisioning", status: "succeeded",
          started_at: "2026-05-25T00:00:03Z", ended_at: "2026-05-25T00:00:30Z",
          last_message: null },
        { phase: "bootstrapping", status: "succeeded",
          started_at: "2026-05-25T00:00:31Z", ended_at: "2026-05-25T00:01:30Z",
          last_message: null },
      ],
    };
    render(<ProvisioningStatus summary={done} />);
    const row = screen.getByTestId("phase-row-ready");
    expect(row.querySelector('[data-icon="check"]')).not.toBeNull();
  });

  it("does not mark ready succeeded when terminal due to failure", () => {
    const failedTerminal: ProvisioningSummary = {
      current_phase: null,
      terminal: true,
      phases: [
        { phase: "provisioning", status: "failed",
          started_at: "2026-05-25T00:00:00Z", ended_at: "2026-05-25T00:00:10Z",
          last_message: "insufficient capacity" },
      ],
    };
    render(<ProvisioningStatus summary={failedTerminal} />);
    const row = screen.getByTestId("phase-row-ready");
    expect(row.querySelector('[data-icon="pending"]')).not.toBeNull();
  });

  it("failed phase shows error icon and red banner", () => {
    const failed: ProvisioningSummary = {
      current_phase: null, terminal: true,
      phases: [
        { phase: "provisioning", status: "failed",
          started_at: "2026-05-25T00:00:00Z", ended_at: "2026-05-25T00:00:10Z",
          last_message: "insufficient capacity" },
      ],
    };
    render(<ProvisioningStatus summary={failed} />);
    const row = screen.getByTestId("phase-row-provisioning");
    expect(row.querySelector('[data-icon="error"]')).not.toBeNull();
    expect(screen.getByText(/insufficient capacity/)).toBeInTheDocument();
  });

  it("displays the running phase's last_message", () => {
    render(<ProvisioningStatus summary={baseSummary} />);
    expect(screen.getByText("creating ec2")).toBeInTheDocument();
  });
});
