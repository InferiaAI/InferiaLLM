import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { CompatibilityPanel } from "../configSections";

vi.mock("@/components/deployment/CompatibilityProjectionChart", () => ({
  CompatibilityProjectionChart: () => <div data-testid="projection-chart" />,
}));

function compat(over: Record<string, unknown> = {}) {
  return {
    fitLevel: "Good",
    requiredVram: 9.4,
    availableVram: 23.5,
    isCompatible: true,
    score: 72,
    estimatedTps: 48,
    reason: "Fits.",
    details: { qualityScore: 70, speedScore: 60, fitScore: 80, contextScore: 50 },
    contextLength: 32768,
    ...over,
  };
}

function renderPanel(over: Record<string, unknown> = {}) {
  return render(
    <CompatibilityPanel
      compatibility={compat(over)}
      selectedPool={{ pool_name: "dc2" }}
      selectedEngine="vllm"
      dispatch={vi.fn()}
    />,
  );
}

describe("CompatibilityPanel", () => {
  it("shows the estimate for a pool whose GPU is known", () => {
    renderPanel();
    expect(screen.getByText(/Compatibility: Good/)).toBeInTheDocument();
    expect(screen.getByText(/72\/100/)).toBeInTheDocument();
    expect(screen.getByTestId("projection-chart")).toBeInTheDocument();
    expect(screen.getByText(/48\.0/)).toBeInTheDocument();
  });

  it("withholds every GPU-derived figure when the GPU is unknown", () => {
    renderPanel({ fitLevel: "Unknown", score: 0, estimatedTps: 0, availableVram: 0 });

    expect(screen.getByText(/Compatibility: Unknown/)).toBeInTheDocument();
    expect(screen.getByText(/no estimate/)).toBeInTheDocument();

    expect(screen.queryByText(/0\/100/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0\.0/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("projection-chart")).not.toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the scores that do not depend on the GPU", () => {
    renderPanel({ fitLevel: "Unknown", score: 0, estimatedTps: 0, availableVram: 0 });

    expect(screen.getByText(/\u{1F48E}/u)).toBeInTheDocument();
    expect(screen.getByText(/\u{1F4CF}/u)).toBeInTheDocument();
    expect(screen.queryByText(/\u{1F3CE}/u)).not.toBeInTheDocument();
    expect(screen.queryByText(/\u{1F9E9}/u)).not.toBeInTheDocument();
  });

  it("still shows context length, which comes from the model not the GPU", () => {
    renderPanel({ fitLevel: "Unknown", score: 0, estimatedTps: 0, availableVram: 0 });
    expect(screen.getByText(/32,768 tokens/)).toBeInTheDocument();
  });
});
