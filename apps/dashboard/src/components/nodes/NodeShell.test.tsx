import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import NodeShell from "./NodeShell";

describe("NodeShell", () => {
  it("shows disabled placeholder when state=provisioning", () => {
    render(<NodeShell nodeId="n1" nodeState="provisioning" currentPhase="pulumi_up" />);
    expect(screen.getByText(/shell available once the worker registers/i))
      .toBeInTheDocument();
    expect(screen.getByText(/pulumi_up/i)).toBeInTheDocument();
  });

  it("falls back to existing WS shell when state=ready", () => {
    render(<NodeShell nodeId="n1" nodeState="ready" />);
    expect(screen.queryByText(/shell available once/i)).not.toBeInTheDocument();
  });

  it("disabled placeholder shows 'pending' when no current phase", () => {
    render(<NodeShell nodeId="n1" nodeState="provisioning" />);
    expect(screen.getByText(/pending/i)).toBeInTheDocument();
  });

  it("renders WS shell when nodeState is not provided (backwards compat)", () => {
    render(<NodeShell nodeId="n1" />);
    expect(screen.queryByText(/shell available once/i)).not.toBeInTheDocument();
  });
});
